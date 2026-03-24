import sqlite3
import sqlite_vec

def test():
    conn = sqlite3.connect('data/EventTracker.db')
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    
    rows = conn.execute("""
        SELECT
            a.rowid as id1,
            b.rowid as id2,
            vec_distance_cosine(a.embedding, b.embedding) AS distance
        FROM entry_embeddings a
        JOIN entry_embeddings b ON a.rowid < b.rowid
        JOIN entries ea ON a.rowid = ea.id
        JOIN entries eb ON b.rowid = eb.id
        WHERE ea.group_id = 1 AND eb.group_id = 1
        ORDER BY distance ASC
        LIMIT 10
    """).fetchall()
    
    for r in rows:
        print(r['id1'], r['id2'], r['distance'])

if __name__ == '__main__':
    test()