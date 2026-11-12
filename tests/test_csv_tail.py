from app.simulation.csv_tail import CSV_TAIL_READER


def test_reader_returns_header_and_complete_tail_of_large_file(tmp_path):
    namespace = {}
    exec(CSV_TAIL_READER, namespace)
    read_tail = namespace["read_csv_tail"]
    path = tmp_path / "live.csv"
    path.write_bytes(b"Time_s,Energy_J\n" + b"1,123456789\n" * 100000 + b"2,50\n3,60\n4,7")
    assert read_tail(path, 2) == "Time_s,Energy_J\n2,50\n3,60\n"
    path.write_bytes(b"Time_s,Energy_J\n1,10\n2,20")
    assert read_tail(path, 2) == "Time_s,Energy_J\n1,10\n"
