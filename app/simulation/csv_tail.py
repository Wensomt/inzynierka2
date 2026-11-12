"""Mały czytnik przesyłany do Pythona w kontenerze, bez instalowania tam aplikacji."""

CSV_TAIL_READER = r'''
def read_csv_tail(path, limit):
    # Odczyt jest ograniczony także w bajtach: nie skanujemy rosnącego pliku
    # od początku przy każdym odświeżeniu GUI.
    with open(path, 'rb') as handle:
        header = handle.readline(65536)
        body_start = handle.tell()
        handle.seek(0, 2)
        start = max(body_start, handle.tell() - 1048576)
        handle.seek(start)
        if start > body_start:
            handle.readline()  # pomiń pierwszy, potencjalnie ucięty wiersz
        rows = handle.read().splitlines(keepends=True)
    if rows and not rows[-1].endswith((b'\n', b'\r')):
        rows.pop()
    return (header + b''.join(rows[-limit:])).decode('utf-8', errors='replace')
'''
