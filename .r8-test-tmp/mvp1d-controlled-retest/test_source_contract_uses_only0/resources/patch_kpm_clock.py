"""Use ns-3 time in KPM headers, inside a disposable session container."""
import pathlib
import sys


def patch(source: str) -> str:
    marker = "// My Energy: KPM uses simulation time in microseconds."
    if marker in source:
        return source
    old = "colstartTime = get_time_now_us ();"
    if source.count(old) != 1:
        raise ValueError("Unsupported KPM source: expected one clock assignment")
    source = source.replace("#include <ns3/log.h>", "#include <ns3/log.h>\n#include <ns3/simulator.h>")
    return source.replace(old, old + "\n  " + marker + "\n  uint64_t simulation_time_us = ns3::Simulator::Now ().GetMicroSeconds ();\n  memcpy (colstartTime.buf, &simulation_time_us, sizeof (simulation_time_us));")


if __name__ == "__main__":
    path = pathlib.Path(sys.argv[1])
    source = path.read_text(encoding="utf-8")
    updated = patch(source)
    if updated != source:
        path.write_text(updated, encoding="utf-8")
    print("KPM clock: ns-3 microseconds")
