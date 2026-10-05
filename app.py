from flask import Flask, request, jsonify
from flask_cors import CORS
import pyodbc
from waitress import serve

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# ПАРАМЕТРЫ ПОДКЛЮЧЕНИЯ К КОРПОРАТИВНОЙ БАЗЕ DWH
# ---------------------------------------------------------------------------
SERVER = "biadhock.np.work"
DATABASE = "DWH"

CONN_STR = f"DRIVER={{SQL Server}};SERVER={SERVER};DATABASE={DATABASE};Trusted_Connection=yes;"

def get_db_connection():
    return pyodbc.connect(CONN_STR)

@app.route('/api/data', methods=['GET'])
def get_data():
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    search_barcode = request.args.get('barcode', '').strip()
    frd_sender = request.args.get('frd_sender', '').strip()
    frd_recipient = request.args.get('frd_recipient', '').strip()

    if not date_from or not date_to:
        return jsonify({'error': 'Параметри date_from та date_to є обов\'язковими'}), 400

    # =======================================================================
    # ВАРИАНТ A: ТОЧЕЧНЫЙ БЫСТРЫЙ ПОИСК (ЕСЛИ ВВЕДЕН ШК / ЭН)
    # =======================================================================
    if search_barcode:
        # 1. Сначала мгновенно находим ID только из таблиц накладных без лишних JOIN
        sql_barcode = """
        SET NOCOUNT ON;
        DROP TABLE IF EXISTS #temp_ewb;

        SELECT DISTINCT
            ewb.iExpressWaybillID,
            ewb.Number AS EN,
            ewb.dt,
            ewb.DateTime,
            ewb.ScheduledDeliveryDate,
            ewb.iCitySenderID,
            ewb.iCityRecipientID,
            ewb.iWarehouseSenderID,
            ewb.iWarehouseRecipientID,
            ewb.iCityDistrictsID
        INTO #temp_ewb
        FROM [DWH].[dm].[DocumentExpressWaybill] ewb (NOLOCK)
        WHERE ewb.DeletionMark = 0
          AND ewb.iOwnerDocumentTypeID <> 7
          AND ewb.Number = ?;

        -- 2. Подтягиваем справочники только к найденным 1-2 строкам
        SELECT
            t.iExpressWaybillID,
            t.EN,
            bp.barcode,
            CONVERT(VARCHAR(10), t.dt, 120) AS 'createDate',
            CONVERT(VARCHAR(19), t.DateTime, 120) AS 'createdDateTime',
            CONVERT(VARCHAR(19), t.ScheduledDeliveryDate, 120) AS 'scheduledDeliveryDate',
            IIF(cat.iCategoryCargoID in (3,4,5), 'В', cat.Code) AS 'cargoType',
            frd1.CityName AS 'frdSender',
            cf1.CityName AS 'citySender',
            IIF(INV1.INVENTLOCATIONID is NULL, INV1a.INVENTLOCATIONID, INV1.INVENTLOCATIONID) AS 'whSender',
            frd2.CityName AS 'frdRecipient',
            cf2.CityName AS 'cityRecipient',
            IIF(INV2.INVENTLOCATIONID is NULL, INV2a.INVENTLOCATIONID, INV2.INVENTLOCATIONID) AS 'whRecipient',
            DATEDIFF(day, t.DateTime, t.ScheduledDeliveryDate) AS 'plannedDays',
            IIF(wish.iExpressWaybillID IS NOT NULL, 1, 0) AS 'hasRequestedDate'
        FROM #temp_ewb t
        JOIN [DWH].[dm].[DocumentExpressWaybill_BarcodeParameters] bp (NOLOCK) 
            ON bp.iExpressWaybillID = t.iExpressWaybillID
        JOIN [DWH].[dim].[dimCatalogCategoryCargo] cat (NOLOCK) ON bp.iCategoryCargoID = cat.iCategoryCargoID
        JOIN dwh.dim.dimCatalogCities cf1 (NOLOCK) ON cf1.iCityID = t.iCitySenderID
        JOIN dwh.dim.dimCatalogCities cf2 (NOLOCK) ON cf2.iCityID = t.iCityRecipientID
        JOIN dwh.dim.dimCatalogCities frd1 (NOLOCK) ON frd1.iCityID = cf1.iRegionalCenterID
        JOIN dwh.dim.dimCatalogCities frd2 (NOLOCK) ON frd2.iCityID = cf2.iRegionalCenterID
        LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV1 (NOLOCK) ON INV1.iWarehouseID = t.iWarehouseSenderID
        LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV2 (NOLOCK) ON INV2.iWarehouseID = t.iWarehouseRecipientID
        LEFT JOIN [DWH].[dim].[dimCatalogCityDistricts] ccdR (NOLOCK) ON ccdR.iCityDistrictsID = t.iCityDistrictsID
        LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV2a (NOLOCK) ON ccdR.iServiceWarehouseID = INV2a.iWarehouseID
        LEFT JOIN [DWH].[dm].[InfoRegExpressWaybillData_Setting_filtered_SenderCityDistrict] ew_dop (NOLOCK) ON ew_dop.iExpressWaybillID = t.iExpressWaybillID
        LEFT JOIN [DWH].[dim].[dimCatalogCityDistricts] ccdS (NOLOCK) ON ccdS.iCityDistrictsID = ew_dop.iCityDistrictsID
        LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV1a (NOLOCK) ON ccdS.iServiceWarehouseID = INV1a.iWarehouseID
        LEFT JOIN (
            SELECT DISTINCT f.iExpressWaybillID
            FROM [DWH].[dm].[InfoRegExpressWaybillData_Setting_filtered] f (NOLOCK)
            JOIN dwh.dm.DocumentProvidedServices dps (NOLOCK) ON dps.iOwnerDocumentID = f.iExpressWaybillID AND dps.iOwnerDocument_TRefID = 6 AND dps.DeletionMark = 0
            JOIN dwh.dm.DocumentProvidedServices_Services dpss (NOLOCK) ON dpss.iProvidedServicesID = dps.iProvidedServicesID 
            WHERE CAST(f.Value_D AS DATE) != '1900-01-01' AND CAST(f.Value_D AS DATE) != '1901-01-01' AND dpss.iServiceID = 17180
        ) wish ON wish.iExpressWaybillID = t.iExpressWaybillID;
        """
        params_barcode = [search_barcode]

        # Быстрое построение точек маршрута через мгновенный поиск в #temp_ewb
        sql_bmp = """
        SET NOCOUNT ON;
        DROP TABLE IF EXISTS #temp_ewb_bmp;

        SELECT DISTINCT
            ewb.iExpressWaybillID,
            ewb.Number AS EN,
            ewb.dt
        INTO #temp_ewb_bmp
        FROM [DWH].[dm].[DocumentExpressWaybill] ewb (NOLOCK)
        WHERE ewb.DeletionMark = 0
          AND ewb.iOwnerDocumentTypeID <> 7
          AND  ewb.Number = ?;

        SELECT
            t.iExpressWaybillID,
            t.EN,
            bp.barcode,
            CONVERT(VARCHAR(10), t.dt, 120) AS 'createDate',
            INV.INVENTLOCATIONID AS 'subdivision',
            WH.Longitude AS 'lng',
            WH.Latitude AS 'lat',
            CONVERT(VARCHAR(19), BMP.ArrivalTime, 120) AS 'arrivalTime',
            CONVERT(VARCHAR(19), BMP.DepartureTime, 120) AS 'departureTime'
        FROM #temp_ewb_bmp t
        JOIN [DWH].[dm].[DocumentExpressWaybill_BarcodeParameters] bp (NOLOCK) 
            ON bp.iExpressWaybillID = t.iExpressWaybillID 
        JOIN [DWH].[dm].[InfoRegBarcodeMovementPlan] BMP (NOLOCK) 
            ON BMP.iExpressWaybillID = t.iExpressWaybillID AND bp.Barcode = BMP.Barcode
        JOIN DWH.dim.dimCatalogInventLocation_AX INV (NOLOCK) 
            ON INV.iWarehouseID = BMP.iWarehouseID
        JOIN [DWH].[dim].[dimCatalogWarehouses] WH (NOLOCK) 
            ON WH.whID = BMP.iWarehouseID
        ORDER BY t.iExpressWaybillID, bp.barcode, BMP.ArrivalTime;
        """
        params_bmp = [search_barcode]

    # =======================================================================
    # ВАРИАНТ Б: ОБЫЧНАЯ ВЫГРУЗКА ЗА ПЕРИОД ПО ФРД (ЕСЛИ ШК НЕ ВВЕДЕН)
    # =======================================================================
    else:
        sql_barcode = """
        SELECT
            ewb.iExpressWaybillID,
            ewb.Number AS 'EN',
            bp.Barcode AS 'barcode',
            CONVERT(VARCHAR(10), ewb.dt, 120) AS 'createDate',
            CONVERT(VARCHAR(19), ewb.DateTime, 120) AS 'createdDateTime',
            CONVERT(VARCHAR(19), ewb.ScheduledDeliveryDate, 120) AS 'scheduledDeliveryDate',
            IIF(cat.iCategoryCargoID in (3,4,5), 'В', cat.Code) AS 'cargoType',
            frd1.CityName AS 'frdSender',
            cf1.CityName AS 'citySender',
            IIF(INV1.INVENTLOCATIONID is NULL, INV1a.INVENTLOCATIONID, INV1.INVENTLOCATIONID) AS 'whSender',
            frd2.CityName AS 'frdRecipient',
            cf2.CityName AS 'cityRecipient',
            IIF(INV2.INVENTLOCATIONID is NULL, INV2a.INVENTLOCATIONID, INV2.INVENTLOCATIONID) AS 'whRecipient',
            DATEDIFF(day, ewb.DateTime, ewb.ScheduledDeliveryDate) AS 'plannedDays',
            IIF(wish.iExpressWaybillID IS NOT NULL, 1, 0) AS 'hasRequestedDate'
        FROM [DWH].[dm].[DocumentExpressWaybill] ewb (nolock)
        JOIN DWH.dm.DocumentExpressWaybill_BarcodeParameters AS bp (nolock) ON bp.iExpressWaybillID = ewb.iExpressWaybillID AND bp.Untied = 0
        JOIN [DWH].[dim].[dimCatalogCategoryCargo] AS cat WITH (nolock) ON bp.iCategoryCargoID = cat.iCategoryCargoID
        JOIN dwh.dim.dimCatalogCities cf1 (nolock) ON cf1.iCityID = ewb.iCitySenderID
        JOIN dwh.dim.dimCatalogCities cf2 (nolock) ON cf2.iCityID = ewb.iCityRecipientID
        JOIN dwh.dim.dimCatalogCities frd1 (nolock) ON frd1.iCityID = cf1.iRegionalCenterID
        JOIN dwh.dim.dimCatalogCities frd2 (nolock) ON frd2.iCityID = cf2.iRegionalCenterID
        LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV1 (nolock) ON INV1.iWarehouseID = ewb.iWarehouseSenderID
        LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV2 (nolock) ON INV2.iWarehouseID = ewb.iWarehouseRecipientID
        JOIN [DWH].[dm].[InfoRegBarcodeMovementPlan] BMP1 (nolock) ON ewb.iExpressWaybillID = BMP1.iExpressWaybillID AND BP.Barcode = BMP1.Barcode AND BMP1.RowNumber = 1
        LEFT JOIN [DWH].[dim].[dimCatalogCityDistricts] ccdR WITH (nolock) ON ccdR.iCityDistrictsID = ewb.iCityDistrictsID
        LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV2a (nolock) ON ccdR.iServiceWarehouseID = INV2a.iWarehouseID
        LEFT JOIN [DWH].[dm].[InfoRegExpressWaybillData_Setting_filtered_SenderCityDistrict] ew_dop WITH (nolock) ON ew_dop.iExpressWaybillID = ewb.iExpressWaybillID
        LEFT JOIN [DWH].[dim].[dimCatalogCityDistricts] AS ccdS (nolock) ON ccdS.iCityDistrictsID = ew_dop.iCityDistrictsID
        LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV1a (nolock) ON ccdS.iServiceWarehouseID = INV1a.iWarehouseID
        LEFT JOIN (
            SELECT DISTINCT f.iExpressWaybillID
            FROM [DWH].[dm].[InfoRegExpressWaybillData_Setting_filtered] f (nolock)
            JOIN dwh.dm.DocumentProvidedServices dps (nolock) ON dps.iOwnerDocumentID = f.iExpressWaybillID AND dps.iOwnerDocument_TRefID = 6 AND dps.DeletionMark = 0
            JOIN dwh.dm.DocumentProvidedServices_Services dpss (nolock) ON dpss.iProvidedServicesID = dps.iProvidedServicesID 
            WHERE CAST(f.Value_D AS DATE) != '1900-01-01' AND CAST(f.Value_D AS DATE) != '1901-01-01' AND dpss.iServiceID = 17180
        ) wish ON wish.iExpressWaybillID = ewb.iExpressWaybillID
        WHERE ewb.iOwnerDocumentTypeID <> 7
          AND ewb.DeletionMark = 0
          AND ewb.dt BETWEEN ? AND ?
        """
        params_barcode = [date_from, date_to]

        if frd_sender:
            sql_barcode += " AND frd1.CityName = ?"
            params_barcode.append(frd_sender)
        if frd_recipient:
            sql_barcode += " AND frd2.CityName = ?"
            params_barcode.append(frd_recipient)

        sql_bmp = """
        SELECT
            ewb.iExpressWaybillID,
            ewb.Number AS 'EN',
            bp.Barcode AS 'barcode',
            CONVERT(VARCHAR(10), ewb.dt, 120) AS 'createDate',
            INV.INVENTLOCATIONID AS 'subdivision',
            WH.Longitude AS 'lng',
            WH.Latitude AS 'lat',
            CONVERT(VARCHAR(19), BMP.ArrivalTime, 120) AS 'arrivalTime',
            CONVERT(VARCHAR(19), BMP.DepartureTime, 120) AS 'departureTime'
        FROM [DWH].[dm].[DocumentExpressWaybill] ewb (nolock)
        JOIN dwh.dim.dimCatalogCities cf1 (nolock) ON cf1.iCityID = ewb.iCitySenderID
        JOIN dwh.dim.dimCatalogCities cf2 (nolock) ON cf2.iCityID = ewb.iCityRecipientID
        JOIN dwh.dim.dimCatalogCities frd1 (nolock) ON frd1.iCityID = cf1.iRegionalCenterID
        JOIN dwh.dim.dimCatalogCities frd2 (nolock) ON frd2.iCityID = cf2.iRegionalCenterID
        JOIN DWH.dm.DocumentExpressWaybill_BarcodeParameters AS bp (nolock) ON bp.iExpressWaybillID = ewb.iExpressWaybillID AND bp.Untied = 0
        JOIN [DWH].[dm].[InfoRegBarcodeMovementPlan] BMP (nolock) ON ewb.iExpressWaybillID = BMP.iExpressWaybillID AND BP.Barcode = BMP.Barcode
        JOIN DWH.dim.dimCatalogInventLocation_AX INV (nolock) ON INV.iWarehouseID = BMP.iWarehouseID
        JOIN [DWH].[dim].[dimCatalogWarehouses] WH (nolock) ON WH.whID = BMP.iWarehouseID
        WHERE ewb.iOwnerDocumentTypeID <> 7
          AND ewb.DeletionMark = 0
          AND ewb.dt BETWEEN ? AND ?
        """
        params_bmp = [date_from, date_to]

        if frd_sender:
            sql_bmp += " AND frd1.CityName = ?"
            params_bmp.append(frd_sender)
        if frd_recipient:
            sql_bmp += " AND frd2.CityName = ?"
            params_bmp.append(frd_recipient)

        sql_bmp += " ORDER BY ewb.iExpressWaybillID, bp.Barcode, BMP.ArrivalTime"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Выполнение Запроса 1 (Посылки)
        cursor.execute(sql_barcode, params_barcode)
        while cursor.description is None:
            if not cursor.nextset():
                break
        cols1 = [column[0] for column in cursor.description]
        parcels = [dict(zip(cols1, row)) for row in cursor.fetchall()]

        # Выполнение Запроса 2 (Маршруты)
        cursor.execute(sql_bmp, params_bmp)
        while cursor.description is None:
            if not cursor.nextset():
                break
        cols2 = [column[0] for column in cursor.description]
        routes = [dict(zip(cols2, row)) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'parcels': parcels,
            'routes': routes
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Сервер успішно запущено на http://127.0.0.1:5000")
    serve(app, host='127.0.0.1', port=5000)