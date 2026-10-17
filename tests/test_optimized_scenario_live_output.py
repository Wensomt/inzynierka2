from pathlib import Path


SCENARIO_SOURCE = (
    Path(__file__).resolve().parents[1] / "resources" / "my_energy_scenario_optimized.cc"
)


def _source():
    return SCENARIO_SOURCE.read_text(encoding="utf-8")


def test_live_timeseries_has_the_complete_declared_schema_and_durable_append():
    source = _source()

    assert (
        "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,"
        "IntervalThroughput_bps,CumulativeEE_bit_per_J,PacketLoss_pct,MeanDelay_ms"
    ) in source
    assert 'outFile.open ("ee_timeseries.csv", std::ios_base::out | std::ios_base::app);' in source
    assert 'outFile.close ();' in source
    assert 'Cannot create live metrics file ee_timeseries.csv' in source


def test_live_timeseries_uses_real_flow_monitor_quality_for_the_downlink_only():
    source = _source()

    assert '#include "ns3/flow-monitor-module.h"' in source
    assert "trackedDlFlowMonitor = trackedFlowMonitorHelper.Install (remoteHostContainer);" in source
    assert "trackedFlowMonitorHelper.Install (ueNodes);" in source
    assert "tuple.destinationPort != TRACKED_DL_PORT" in source
    assert "trackedDlFlowMonitor->CheckForLostPackets ();" in source
    # Live loss counts declared drops/timeouts, while the final finite-window
    # delivery failure ratio also includes packets outstanding after drain.
    live = source[source.index("ReadTrackedDlFlowQuality ("):source.index("WriteEeTimeseries ()")]
    assert "quality.monitorLostPackets" in live
    assert "quality.txPackets - quality.rxPackets" in source
    assert "not_received_by_measurement_end" in source


def test_scenario_stops_traffic_before_measurement_end_and_preserves_counters():
    source = _source()
    assert "clientApp.Stop(Seconds(trafficStopS));" in source
    assert "const double trafficStopS = simTime - drainTimeS;" in source
    assert "ReadTotalRadioEnergyJ ();" in source
    assert 'SerializeToXmlFile ("flow-monitor.xml", false, false)' in source
    assert "tx_packets,rx_packets,lost_packets,monitor_lost_packets,delay_sum_s" in source


def test_energy_getter_is_not_called_twice_at_the_same_simulation_instant():
    source = _source()
    assert "lastRadioEnergyReadTime == nowS" in source
    assert "lastRadioEnergyReadTime = -1.0;" in source


def test_live_timeseries_is_sampled_during_run_and_once_at_exact_end_time():
    source = _source()

    periodic_schedule = 'Simulator::Schedule (Seconds (i * simTime / numPrints + 0.000001), &WriteEeTimeseries);'
    endpoint_schedule = 'Simulator::Schedule (Seconds (simTime), &WriteEeTimeseries);'
    run_position = source.index("Simulator::Run();")
    final_sample_position = source.index("WriteEeTimeseries ();", run_position)
    summary_position = source.index('summaryFile.open("ee_summary.csv"', run_position)

    assert periodic_schedule in source
    assert endpoint_schedule in source
    assert run_position < final_sample_position < summary_position


def test_topology_rows_are_scheduled_and_closed_for_live_reading():
    source = _source()

    assert "&PrintGnuplottableEnbListToFile" in source
    assert "&PrintPosition" in source
    assert "Simulator::Schedule(Seconds(simTime), &PrintGnuplottableEnbListToFile" in source
    assert "Simulator::Schedule(Seconds(simTime), &PrintPosition" in source
    assert 'outFile2.close();' in source
    assert 'outFile.close ();' in source


def test_live_output_does_not_switch_the_ric_to_offline_file_logging():
    source = _source()

    marker = '"If true, generate offline file logging instead of connecting to RIC",\n                              ns3::BooleanValue(false)'
    assert marker in source
