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

    # Даты обязательны для оптимизации запросов в DWH
    if not date_from or not date_to:
        return jsonify({'error': 'Параметри date_from та date_to є обов\'язковими'}), 400

    # 1. Запрос Barcode (Основные данные посылок)
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
        DATEDIFF(day, ewb.DateTime, ewb.ScheduledDeliveryDate) AS 'plannedDays'
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
    WHERE ewb.iOwnerDocumentTypeID <> 7
      AND ewb.DeletionMark = 0
      AND ewb.dt BETWEEN ? AND ?
    """
    params_barcode = [date_from, date_to]

    if search_barcode:
        sql_barcode += " AND bp.Barcode = ?"
        params_barcode.append(search_barcode)
    if frd_sender:
        sql_barcode += " AND frd1.CityName = ?"
        params_barcode.append(frd_sender)
    if frd_recipient:
        sql_barcode += " AND frd2.CityName = ?"
        params_barcode.append(frd_recipient)

    # 2. Запрос Barcode_BMP (Узлы маршрута)
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

    if search_barcode:
        sql_bmp += " AND bp.Barcode = ?"
        params_bmp.append(search_barcode)
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

        cursor.execute(sql_barcode, params_barcode)
        cols1 = [column[0] for column in cursor.description]
        parcels = [dict(zip(cols1, row)) for row in cursor.fetchall()]

        cursor.execute(sql_bmp, params_bmp)
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