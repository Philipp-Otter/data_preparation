import time
from threading import Thread

import psycopg2
from tqdm import tqdm

from src.core.config import settings
from src.db.db import Database
from src.utils.utils import print_error, print_info


class ProcessSegments(Thread):

    def __init__(
            self,
            thread_id: int,
            db_local: Database,
            get_next_h3_index,
            cycling_surfaces
        ):
        super().__init__(group=None, target=self)

        self.thread_id = thread_id
        self.db_local = db_local
        self.get_next_h3_index = get_next_h3_index
        self.cycling_surfaces = cycling_surfaces


    def run(self):
        """Process segment data for this H3 index region"""

        connection_string = f"dbname={settings.POSTGRES_DB} user={settings.POSTGRES_USER} \
                            password={settings.POSTGRES_PASSWORD} host={settings.POSTGRES_HOST} \
                            port={settings.POSTGRES_PORT}"
        conn = psycopg2.connect(connection_string)
        cur = conn.cursor()

        h3_index = self.get_next_h3_index()
        while h3_index is not None:
            # Get all segment IDs for this H3 index
            sql_get_segment_ids = f"""
                SELECT s.id, ST_AsText(g.h3_boundary) FROM
                temporal.segments s, basic.h3_3_grid g
                WHERE
                ST_Intersects(ST_Centroid(s.geometry), g.h3_geom)
                AND g.h3_index = '{h3_index}';
            """
            segment_ids = cur.execute(sql_get_segment_ids)
            segment_ids = cur.fetchall()

            # Process each segment
            for index in tqdm(range(len(segment_ids)), desc=f"Thread {self.thread_id} - H3 index [{h3_index}]", unit=" segments", mininterval=1, smoothing=0.0):
                id = segment_ids[index]
                sql_classify_segment = f"""
                    SELECT classify_segment(
                        '{id[0]}',
                        '{self.cycling_surfaces}'::jsonb,
                        '{id[1]}'
                    );
                """
                try:
                    cur.execute(sql_classify_segment)
                    if index % 1000 == 0:
                        conn.commit()
                except Exception as e:
                    print_error(f"Thread {self.thread_id} failed to process segment {h3_index}, error: {e}.")
                    break

            h3_index = self.get_next_h3_index()

        conn.close()


class UpdateImpedance(Thread):

    def __init__(
            self,
            thread_id: int,
            db_local: Database,
            get_next_h3_index,
        ):
        super().__init__(group=None, target=self)

        self.thread_id = thread_id
        self.db_local = db_local
        self.get_next_h3_index = get_next_h3_index


    def run(self):
        """Update slope impedance data for this H3 index region"""

        connection_string = f"dbname={settings.POSTGRES_DB} user={settings.POSTGRES_USER} \
                            password={settings.POSTGRES_PASSWORD} host={settings.POSTGRES_HOST} \
                            port={settings.POSTGRES_PORT}"
        conn = psycopg2.connect(connection_string)
        cur = conn.cursor()

        h3_index = self.get_next_h3_index()
        while h3_index is not None:
            sql_update_impedance = f"""
                WITH segment AS (
                    SELECT id, length_m, geom
                    FROM basic.segments_processed
                    WHERE h3_5[1] = {h3_index}
                )
                UPDATE basic.segments_processed AS sp
                SET impedance_slope = c.imp, impedance_slope_reverse = c.rs_imp
                FROM segment,
                LATERAL get_slope_profile(segment.geom, segment.length_m, ST_LENGTH(segment.geom)) s,
                LATERAL compute_impedances(s.elevs, s.linklength, s.lengthinterval) c
                WHERE sp.id = segment.id;
            """
            try:
                start_time = time.time()
                cur.execute(sql_update_impedance)
                conn.commit()
                print_info(f"Thread {self.thread_id} updated impedance for H3 index {h3_index}. Time: {round(time.time() - start_time)} seconds.")
            except Exception as e:
                print_error(f"Thread {self.thread_id} failed to update impedances for H3 index {h3_index}, error: {e}.")
                break

            h3_index = self.get_next_h3_index()

        conn.close()
