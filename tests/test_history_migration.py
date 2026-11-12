import json

from app.data.storage import SimulationStorage
from app.simulation.models import Simulation, SimulationConfiguration, SimulationResult, SimulationStatus


def test_new_parser_can_remove_an_invalid_number_and_keeps_a_backup(tmp_path):
    storage = SimulationStorage(tmp_path)
    item = Simulation("Stary wynik", SimulationConfiguration("optimized", {}), status=SimulationStatus.COMPLETED,
                      result=SimulationResult(metrics={"total_energy_j": 300}, metadata={"result_parser_version": 4}))
    directory = storage.create(item)
    storage.save(item)
    for cell, time_s, energy in ((1, 10, 100), (2, 9, 200)):
        (directory / "timeseries" / f"energyfilecell{cell}.csv").write_text(f"Time,NetEnergy\n{time_s},{energy}\n", encoding="utf-8")
    loaded = storage.load(item.id)
    assert loaded.result.metrics["total_energy_j"] is None
    backup = json.loads((directory / "result.before-parser-4.json").read_text(encoding="utf-8"))
    assert backup["metrics"]["total_energy_j"] == 300


def test_history_recovers_summary_even_without_a_previous_result(tmp_path):
    storage = SimulationStorage(tmp_path)
    item = Simulation("Z podsumowania", SimulationConfiguration("optimized", {}), status=SimulationStatus.COMPLETED)
    directory = storage.create(item)
    (directory / "timeseries" / "ee_summary.csv").write_text("simTime_s,totalEnergy_J,deliveredData_bits\n1,20,1000000\n", encoding="utf-8")
    assert storage.load(item.id).result.metrics["total_energy_j"] == 20
