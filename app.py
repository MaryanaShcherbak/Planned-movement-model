from flask import Flask, request, jsonify
from flask_cors import CORS
import pyodbc
from waitress import serve

app = Flask(__name__)
CORS(app)

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
    min_days = request.args.get('min_days', '4').strip() # Получаем количество дней (по умолчанию 4)

    if not date_from or not date_to:
        return jsonify({'error': 'Параметри date_from та date_to є обов\'язковими'}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # =======================================================================
        # ВАРИАНТ A: ТОЧЕЧНЫЙ БЫСТРЫЙ ПОИСК ПО ШК / ЕН
        # =======================================================================
        if search_barcode:
            sql_barcode = """
            SET NOCOUNT ON;
            DROP TABLE IF EXISTS #temp_ewb;

            SELECT
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
                COALESCE(INV1.INVENTLOCATIONID, INV1a.INVENTLOCATIONID) AS 'whSender',
                frd2.CityName AS 'frdRecipient',
                cf2.CityName AS 'cityRecipient',
                COALESCE(INV2.INVENTLOCATIONID, INV2a.INVENTLOCATIONID) AS 'whRecipient',
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
                WHERE f.Value_D >= '1901-01-02' AND dpss.iServiceID = 17180
            ) wish ON wish.iExpressWaybillID = t.iExpressWaybillID;
            """
            params_barcode = [search_barcode]

            sql_bmp = """
            SET NOCOUNT ON;
            DROP TABLE IF EXISTS #temp_ewb;
            
            SELECT
                ewb.iExpressWaybillID,
                ewb.Number,
                ewb.dt
            INTO #temp_ewb
            FROM [DWH].[dm].[DocumentExpressWaybill] ewb (NOLOCK)
            WHERE ewb.DeletionMark = 0
              AND ewb.iOwnerDocumentTypeID <> 7
              AND ewb.Number = ?;

            SELECT
                ewb.iExpressWaybillID,
                ewb.Number AS EN,
                bp.Barcode AS barcode,
                CONVERT(VARCHAR(10), ewb.dt, 120) AS 'createDate',
                INV.INVENTLOCATIONID AS 'subdivision',
                WH.Longitude AS 'lng',
                WH.Latitude AS 'lat',
                CONVERT(VARCHAR(19), BMP.ArrivalTime, 120) AS 'arrivalTime',
                CONVERT(VARCHAR(19), BMP.DepartureTime, 120) AS 'departureTime'
            FROM #temp_ewb ewb (NOLOCK) 
            JOIN [DWH].[dm].[DocumentExpressWaybill_BarcodeParameters] bp (NOLOCK)
                ON ewb.iExpressWaybillID = bp.iExpressWaybillID
            JOIN [DWH].[dm].[InfoRegBarcodeMovementPlan] BMP (NOLOCK) 
                ON BMP.iExpressWaybillID = ewb.iExpressWaybillID AND BMP.Barcode = bp.Barcode
            JOIN DWH.dim.dimCatalogInventLocation_AX INV (NOLOCK) 
                ON INV.iWarehouseID = BMP.iWarehouseID
            JOIN [DWH].[dim].[dimCatalogWarehouses] WH (NOLOCK) 
                ON WH.whID = BMP.iWarehouseID
            WHERE bp.Untied = 0
            ORDER BY ewb.iExpressWaybillID, bp.Barcode, BMP.ArrivalTime;
            """
            params_bmp = [search_barcode]

            cursor.execute(sql_barcode, params_barcode)
            while cursor.description is None:
                if not cursor.nextset(): break
            cols1 = [column[0] for column in cursor.description]
            parcels = [dict(zip(cols1, row)) for row in cursor.fetchall()]

            cursor.execute(sql_bmp, params_bmp)
            while cursor.description is None:
                if not cursor.nextset(): break
            cols2 = [column[0] for column in cursor.description]
            routes = [dict(zip(cols2, row)) for row in cursor.fetchall()]

            return jsonify({'parcels': parcels, 'routes': routes})

        # =======================================================================
        # ВАРИАНТ Б: ОБЩАЯ ВЫГРУЗКА ЗА ПЕРИОД
        # =======================================================================
        else:
            frd_joins_step1 = ""
            frd_where_step1 = ""
            params_barcode = [date_from, date_to, int(min_days)]

            if frd_sender:
                frd_joins_step1 += """
                    JOIN dwh.dim.dimCatalogCities cf1_s (NOLOCK) ON cf1_s.iCityID = ewb.iCitySenderID
                    JOIN dwh.dim.dimCatalogCities frd1_s (NOLOCK) ON frd1_s.iCityID = cf1_s.iRegionalCenterID"""
                frd_where_step1 += "\n        AND frd1_s.CityName = ?"
                params_barcode.append(frd_sender)

            if frd_recipient:
                frd_joins_step1 += """
                    JOIN dwh.dim.dimCatalogCities cf2_r (NOLOCK) ON cf2_r.iCityID = ewb.iCityRecipientID
                    JOIN dwh.dim.dimCatalogCities frd2_r (NOLOCK) ON frd2_r.iCityID = cf2_r.iRegionalCenterID"""
                frd_where_step1 += "\n        AND frd2_r.CityName = ?"
                params_barcode.append(frd_recipient)

            sql_barcode = f"""
            SET NOCOUNT ON;

            DECLARE @StartDate DATE = ?;
            DECLARE @EndDate DATE = ?;
            SET @EndDate = DATEADD(day, 1, @EndDate);

            DROP TABLE IF EXISTS #base_ewb;

            SELECT
                ewb.iExpressWaybillID,
                ewb.Number AS EN,
                bp.Barcode AS barcode,
                ewb.dt,
                ewb.DateTime AS createdDateTime,
                ewb.ScheduledDeliveryDate,
                bp.iCategoryCargoID,
                ewb.iCitySenderID,
                ewb.iCityRecipientID,
                ewb.iWarehouseSenderID,
                ewb.iWarehouseRecipientID,
                ewb.iCityDistrictsID,
                DATEDIFF(day, ewb.DateTime, ewb.ScheduledDeliveryDate) AS 'plannedDays'
            INTO #base_ewb
            FROM [DWH].[dm].[DocumentExpressWaybill] ewb (NOLOCK)
            JOIN DWH.dm.DocumentExpressWaybill_BarcodeParameters bp (NOLOCK) 
                ON bp.iExpressWaybillID = ewb.iExpressWaybillID AND bp.Untied = 0
            JOIN [DWH].[dm].[InfoRegBarcodeMovementPlan] BMP1 (NOLOCK) 
                ON BMP1.iExpressWaybillID = ewb.iExpressWaybillID 
               AND BMP1.Barcode = bp.Barcode 
               AND BMP1.RowNumber = 1{frd_joins_step1}
            WHERE ewb.iOwnerDocumentTypeID <> 7
              AND ewb.DeletionMark = 0
              AND ewb.dt >= @StartDate AND ewb.dt < @EndDate
              AND DATEDIFF(day, ewb.DateTime, ewb.ScheduledDeliveryDate) >= ?{frd_where_step1};

            CREATE CLUSTERED INDEX CIX_base_ewb ON #base_ewb(iExpressWaybillID);

            DROP TABLE IF EXISTS #base_with_wish;

            SELECT
                b.*,
                IIF(EXISTS (
                    SELECT 1
                    FROM [DWH].[dm].[InfoRegExpressWaybillData_Setting_filtered] f (NOLOCK)
                    JOIN dwh.dm.DocumentProvidedServices dps (NOLOCK) 
                        ON dps.iOwnerDocumentID = f.iExpressWaybillID 
                       AND dps.iOwnerDocument_TRefID = 6 
                       AND dps.DeletionMark = 0
                    JOIN dwh.dm.DocumentProvidedServices_Services dpss (NOLOCK) 
                        ON dpss.iProvidedServicesID = dps.iProvidedServicesID
                    WHERE f.iExpressWaybillID = b.iExpressWaybillID
                      AND f.Value_D >= '1901-01-02'
                      AND dpss.iServiceID = 17180
                ), 1, 0) AS hasRequestedDate
            INTO #base_with_wish
            FROM #base_ewb b;

            SELECT
                b.iExpressWaybillID,
                b.EN,
                b.barcode,
                CONVERT(VARCHAR(10), b.dt, 120) AS createDate,
                CONVERT(VARCHAR(19), b.createdDateTime, 120) AS createdDateTime,
                CONVERT(VARCHAR(19), b.ScheduledDeliveryDate, 120) AS scheduledDeliveryDate,
                IIF(cat.iCategoryCargoID IN (3, 4, 5), 'В', cat.Code) AS cargoType,
                frd1.CityName AS frdSender,
                cf1.CityName AS citySender,
                COALESCE(INV1.INVENTLOCATIONID, INV1a.INVENTLOCATIONID) AS whSender,
                frd2.CityName AS frdRecipient,
                cf2.CityName AS cityRecipient,
                COALESCE(INV2.INVENTLOCATIONID, INV2a.INVENTLOCATIONID) AS whRecipient,
                DATEDIFF(DAY, b.createdDateTime, b.ScheduledDeliveryDate) AS plannedDays,
                b.hasRequestedDate
            FROM #base_with_wish b
            JOIN [DWH].[dim].[dimCatalogCategoryCargo] cat (NOLOCK) 
                ON cat.iCategoryCargoID = b.iCategoryCargoID
            JOIN dwh.dim.dimCatalogCities cf1 (NOLOCK) 
                ON cf1.iCityID = b.iCitySenderID
            JOIN dwh.dim.dimCatalogCities cf2 (NOLOCK) 
                ON cf2.iCityID = b.iCityRecipientID
            JOIN dwh.dim.dimCatalogCities frd1 (NOLOCK) 
                ON frd1.iCityID = cf1.iRegionalCenterID
            JOIN dwh.dim.dimCatalogCities frd2 (NOLOCK) 
                ON frd2.iCityID = cf2.iRegionalCenterID
            LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV1 (NOLOCK) 
                ON INV1.iWarehouseID = b.iWarehouseSenderID
            LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV2 (NOLOCK) 
                ON INV2.iWarehouseID = b.iWarehouseRecipientID
            LEFT JOIN [DWH].[dim].[dimCatalogCityDistricts] ccdR (NOLOCK) 
                ON ccdR.iCityDistrictsID = b.iCityDistrictsID
            LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV2a (NOLOCK) 
                ON INV2a.iWarehouseID = ccdR.iServiceWarehouseID
            LEFT JOIN [DWH].[dm].[InfoRegExpressWaybillData_Setting_filtered_SenderCityDistrict] ew_dop (NOLOCK) 
                ON ew_dop.iExpressWaybillID = b.iExpressWaybillID
            LEFT JOIN [DWH].[dim].[dimCatalogCityDistricts] ccdS (NOLOCK) 
                ON ccdS.iCityDistrictsID = ew_dop.iCityDistrictsID
            LEFT JOIN DWH.dim.dimCatalogInventLocation_AX INV1a (NOLOCK) 
                ON INV1a.iWarehouseID = ccdS.iServiceWarehouseID;
            """

            cursor.execute(sql_barcode, params_barcode)
            while cursor.description is None:
                if not cursor.nextset(): break
            cols1 = [column[0] for column in cursor.description]
            parcels = [dict(zip(cols1, row)) for row in cursor.fetchall()]

            return jsonify({'parcels': parcels, 'routes': []})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

# ---------------------------------------------------------------------------
# 2. ПОЛУЧЕНИЕ МАРШРУТА ОДНОГО ШК / ЕН НА ВИМОГУ (ON DEMAND)
# ---------------------------------------------------------------------------
@app.route('/api/route', methods=['GET'])
def get_single_route():
    en = request.args.get('en', '').strip()
    barcode = request.args.get('barcode', '').strip()
    
    if not barcode and not en:
        return jsonify({'error': 'Параметри en та barcode є обов\'язковими'}), 400

    sql_bmp = """
    SET NOCOUNT ON;
    DROP TABLE IF EXISTS #temp_ewb;

    SELECT 
        ewb.iExpressWaybillID,
        ewb.Number,
        ewb.dt
    INTO #temp_ewb
    FROM [DWH].[dm].[DocumentExpressWaybill] ewb (NOLOCK)
    WHERE ewb.Number = ?;

    SELECT  
        ewb.iExpressWaybillID,
        ewb.Number AS EN,
        bp.Barcode AS barcode,
        CONVERT(VARCHAR(10), ewb.dt, 120) AS 'createDate',
        INV.INVENTLOCATIONID AS 'subdivision',
        WH.Longitude AS 'lng',
        WH.Latitude AS 'lat',
        CONVERT(VARCHAR(19), BMP.ArrivalTime, 120) AS 'arrivalTime',
        CONVERT(VARCHAR(19), BMP.DepartureTime, 120) AS 'departureTime'
    FROM #temp_ewb ewb (NOLOCK)
    JOIN DWH.dm.DocumentExpressWaybill_BarcodeParameters bp (NOLOCK) on bp.iExpressWaybillID = ewb.iExpressWaybillID AND bp.Untied = 0
    JOIN [DWH].[dm].[InfoRegBarcodeMovementPlan] BMP (NOLOCK) 
        ON BMP.iExpressWaybillID = ewb.iExpressWaybillID AND BMP.Barcode = bp.Barcode
    JOIN DWH.dim.dimCatalogInventLocation_AX INV (NOLOCK) 
        ON INV.iWarehouseID = BMP.iWarehouseID
    JOIN [DWH].[dim].[dimCatalogWarehouses] WH (NOLOCK) 
        ON WH.whID = BMP.iWarehouseID
    WHERE bp.Barcode = ?
    ORDER BY BMP.ArrivalTime;
    """

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql_bmp, [en, barcode])
        
        while cursor.description is None:
            if not cursor.nextset(): 
                break
                
        cols = [column[0] for column in cursor.description]
        routes = [dict(zip(cols, row)) for row in cursor.fetchall()]

        return jsonify({'routes': routes})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    print("Сервер успішно запущено на http://127.0.0.1:5000")
    serve(app, host='127.0.0.1', port=5000)