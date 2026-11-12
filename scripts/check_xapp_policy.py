"""Compile and exercise actual C policy functions in an existing session container."""
import argparse
import io
import shlex
import tarfile

import docker

from app.config import BASE_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    container = docker.from_env().containers.get(args.container)
    root = "/workspace/flexric"
    source_dir = root + "/examples/xApp/c/orange"
    build_dir = root + "/build/examples/xApp/c/orange"
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        for name, path in (("my_energy_policy_source.c", BASE_DIR / "resources/xapp_my_energy_optimized.c"),
                           ("my_energy_policy_checks.c", BASE_DIR / "tests/my_energy_policy_checks.c")):
            data = path.read_bytes()
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    container.put_archive(source_dir, payload.getvalue())
    def run(command):
        code, output = container.exec_run(["bash", "-lc", command], workdir=build_dir)
        text = output.decode("utf-8", errors="replace")
        if code:
            raise RuntimeError(text)
        return text
    flags = run("cat CMakeFiles/xapp_my_energy_optimized.dir/flags.make")
    # CMake flags contain escaped quotes intended for a shell command.
    options = " ".join(line.split(" = ", 1)[1] for line in flags.splitlines() if line.startswith(("C_DEFINES = ", "C_INCLUDES = ", "C_FLAGS = ")))
    run("/usr/bin/cc {} -c {} -o /tmp/my_energy_policy_checks.o".format(options, shlex.quote(source_dir + "/my_energy_policy_checks.c")))
    link = run("cat CMakeFiles/xapp_my_energy_optimized.dir/link.txt").strip()
    link = link.replace("CMakeFiles/xapp_my_energy_optimized.dir/xapp_my_energy_optimized.c.o", "/tmp/my_energy_policy_checks.o")
    link = link.replace("-o xapp_my_energy_optimized", "-o /tmp/my_energy_policy_checks")
    run(link)
    print(run("/tmp/my_energy_policy_checks"))


if __name__ == "__main__":
    main()
