import json
from src.config.config import Config
from src.db.db import Database
from src.core.config import settings
from src.db.tables.poi import POITable

class PublicTransportStopPreparation:
    """Class to prepare/ classify public transport stops of the GTFS dataset. It processes the stops in batches and adds the route types (e.g. 3 = bus) to classify them."""

    def __init__(self, db: Database, region: str):
        self.db = db
        self.region = region
        self.data_config = Config("public_transport_stop", region)
        self.data_config_preparation = self.data_config.preparation

    def run(self):
        """Run the public transport stop preparation."""

        # get the geometires of the study area based on the query defined in the config
        region_geoms = self.db.select(self.data_config_preparation['region'])

        # Create table for public transport stops
        self.db.perform(POITable(data_set_type="poi", schema_name="basic", data_set_name="public_transport_stop").create_poi_table(table_type='standard'))

        # loops through the geometries of the study area and classifies the public transport stops based on GTFS
        # loops through the gtfs stops and classifies them based on the route type in the stop_times table
        for geom in region_geoms:
            classify_gtfs_stop_sql = f"""
                INSERT INTO basic.poi_public_transport_stop(
                    category,
                    name,
                    source,
                    tags,
                    geom
                )
                WITH parent_station_name AS (
                    SELECT s.stop_name AS name, s.stop_id
                    FROM gtfs.stops s
                    WHERE ST_Intersects(s.stop_loc, ST_SetSRID(ST_GeomFromText(ST_AsText('{geom[0]}')), 4326))
                    AND parent_station IS NULL
                ),
                clipped_gfts_stops AS (
                    SELECT p.name, s.stop_loc AS geom, json_build_object('stop_id', s.stop_id, 'parent_station', s.parent_station) AS tags
                    FROM gtfs.stops s, parent_station_name p
                    WHERE ST_Intersects(s.stop_loc, ST_SetSRID(ST_GeomFromText(ST_AsText('{geom[0]}')), 4326))
                    AND parent_station IS NOT NULL
                    AND s.parent_station = p.stop_id
                ),
                categorized_gtfs_stops AS (
                    SELECT route_type, c.name, c.geom, c.tags
                    FROM clipped_gfts_stops c
                    CROSS JOIN LATERAL
                    (
                        SELECT  '{json.dumps(self.data_config_preparation['classification']['gtfs_route_types'])}'::jsonb ->> r.route_type::TEXT AS route_type
                        FROM
                        (
                            SELECT DISTINCT o.route_type
                            FROM gtfs.stop_times_optimized o
                            WHERE o.stop_id = tags ->> 'stop_id'
                            AND o.route_type IN {tuple(int(key) for key in self.data_config_preparation['classification']['gtfs_route_types'].keys())}
                        ) r
                        WHERE route_type IS NOT NULL
                        ORDER BY r.route_type
                    ) j
                )
                SELECT route_type AS category, name, 'gtfs.de' AS source, json_build_object('extended_source', json_build_object('stop_id', ARRAY_AGG(tags ->> 'stop_id'))) AS tags, ST_MULTI(ST_UNION(geom)) AS geom
                FROM categorized_gtfs_stops
                GROUP BY route_type, tags ->> 'parent_station', name
                ;
            """
            #TODO: in der subscription definiere, dass public transport entries von OSM und z.B. Bus von GTFS

            self.db.perform(classify_gtfs_stop_sql)

def prepare_public_transport_stop(region: str):

    db_rd = Database(settings.RAW_DATABASE_URI)
    public_transport_stop_preparation = PublicTransportStopPreparation(db=db_rd, region=region)
    public_transport_stop_preparation.run()