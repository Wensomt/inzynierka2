/* -*-  Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/* *
 * Copyright (c) 2024 Orange Innovation Poland
 * Copyright (c) 2024 Orange Innovation Egypt
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation;
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 *
 * Authors: Andrea Lacava <thecave003@gmail.com>
 *          Michele Polese <michele.polese@gmail.com>
 *          Argha Sen <arghasen10@gmail.com>
 *          Kamil Kociszewski <kamil.kociszewski@orange.com>
 *          Mostafa Ashraf <mostafa.ashraf.ext@orange.com>
 */
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/applications-module.h"
#include "ns3/point-to-point-helper.h"
#include <ns3/lte-ue-net-device.h>
#include "ns3/mmwave-helper.h"
#include "ns3/epc-helper.h"
#include "ns3/mmwave-point-to-point-epc-helper.h"
#include "../src/mmwave/model/node-container-manager.h"
#include "ns3/lte-helper.h"
#include <sys/time.h>
#include <ctime>
#include <sys/types.h>
#include <iostream>
#include <stdlib.h>
#include <list>
#include <random>
#include <chrono>
#include <cmath>
#include <fstream>
#include <vector>
#include <map>
#include <set>
#include <algorithm>
#include <iomanip>
#include <sstream>
#include "ns3/packet-sink.h"
#include "ns3/udp-client.h"
#include "ns3/seq-ts-header.h"
#include "ns3/basic-energy-source-helper.h"
#include "ns3/mmwave-radio-energy-model-enb-helper.h"
#include "ns3/isotropic-antenna-model.h"

using namespace ns3;
using namespace mmwave;

static const uint32_t MAX_TRACKED_UES = 512;
static const uint32_t MAX_TRACKED_CELLS = 512;

std::map<uint64_t, uint16_t> imsi_cellid;
std::map<uint16_t, std::set<uint64_t>> imsi_list;
std::map<uint16_t, Ptr < Node>>
    cellid_node;
std::map<uint32_t, uint16_t> ue_cellid_usinghandover;
std::map<uint64_t, uint32_t> ueimsi_nodeid;
int ue_assoc_list[MAX_TRACKED_UES] = {0};
double maxXAxis;
double maxYAxis;
bool esON_list[MAX_TRACKED_CELLS] = {0};
double totalnewEnergyConsumption_storage[MAX_TRACKED_CELLS] = {0};
double totaloldEnergyConsumption_storage[MAX_TRACKED_CELLS] = {0};
double current_energy_consumption[MAX_TRACKED_CELLS] = {0};
double curr_total_energy_consumption = 0;
double max_energy_consumption = 0;
double sum_curr_total_energy_consumption = 0;
int num_of_mmdev = 0;
ApplicationContainer trackedDlSinks;
energy::DeviceEnergyModelContainer trackedEnergyModels;
// MC PDCP removes byte tags, so FlowMonitor cannot observe receives here.
// Measure the actual UDP applications instead; SeqTsHeader survives the radio path.
ApplicationContainer trackedDlClients;
uint32_t trackedUdpPacketSize = 200;
uint64_t trackedDlRxPackets = 0;
double trackedDlDelaySeconds = 0.0;

void
TrackDlReceive (Ptr<const Packet> packet, const Address& from)
{
  SeqTsHeader header;
  if (packet->GetSize () < header.GetSerializedSize ())
    {
      return;
    }
  packet->PeekHeader (header);
  ++trackedDlRxPackets;
  trackedDlDelaySeconds += (Simulator::Now () - header.GetTs ()).GetSeconds ();
}

double previousEeSampleTime = 0.0;
double previousEeSampleEnergyJ = 0.0;
double previousEeSampleDeliveredBits = 0.0;
// MmWaveRadioEnergyModelEnb::GetTotalEnergyConsumption() integrates the
// current state to Simulator::Now(), but deliberately keeps its internal
// last-update timestamp unchanged.  Repeating the getter at the same
// simulation instant would therefore trip its positive-duration assertion.
double lastRadioEnergyReadTime = -1.0;
double lastRadioEnergyReadValueJ = 0.0;

// Energy includes NR radio models only. LTE anchor, transport, core, UE,
// controller and cell transition energy are outside this model's boundary.
double
ReadTotalRadioEnergyJ ()
{
  const double nowS = Simulator::Now ().GetSeconds ();
  if (lastRadioEnergyReadTime == nowS)
    {
      return lastRadioEnergyReadValueJ;
    }
  double total = 0.0;
  for (uint32_t i = 0; i < trackedEnergyModels.GetN (); ++i)
    {
      // The trace changes only on radio state transitions. The getter also
      // integrates the final residence time in the current state.
      total += trackedEnergyModels.Get (i)->GetTotalEnergyConsumption ();
    }
  lastRadioEnergyReadTime = nowS;
  lastRadioEnergyReadValueJ = total;
  return total;
}

struct DlFlowQuality
{
  uint64_t txPackets = 0;
  uint64_t rxPackets = 0;
  double delaySeconds = 0.0;
};

DlFlowQuality
ReadTrackedDlFlowCounters ()
{
  DlFlowQuality quality;
  for (uint32_t i = 0; i < trackedDlClients.GetN (); ++i)
    {
      Ptr<UdpClient> client = DynamicCast<UdpClient> (trackedDlClients.Get (i));
      if (client)
        {
          quality.txPackets += client->GetTotalTx () / trackedUdpPacketSize;
        }
    }
  quality.rxPackets = trackedDlRxPackets;
  quality.delaySeconds = trackedDlDelaySeconds;
  return quality;
}

static inline bool
IsValidCellIndex(uint32_t cellId)
{
  return cellId < MAX_TRACKED_CELLS;
}

static inline bool
IsValidUeIndex(uint64_t imsi)
{
  return imsi > 0 && imsi <= MAX_TRACKED_UES;
}


/**
 * Scenario Zero
 *
 */

NS_LOG_COMPONENT_DEFINE ("ScenarioZero");

void
PrintGnuplottableUeListToFile(std::string filename) {
    std::ofstream outFile;
    outFile.open(filename.c_str(), std::ios_base::out | std::ios_base::trunc);
    if (!outFile.is_open()) {
        NS_LOG_ERROR("Can't open file " << filename);
        return;
    }
    for (NodeList::Iterator it = NodeList::Begin(); it != NodeList::End(); ++it) {
        Ptr <Node> node = *it;
        int nDevs = node->GetNDevices();
        for (int j = 0; j < nDevs; j++) {
            Ptr <LteUeNetDevice> uedev = node->GetDevice(j)->GetObject<LteUeNetDevice>();
            Ptr <MmWaveUeNetDevice> mmuedev = node->GetDevice(j)->GetObject<MmWaveUeNetDevice>();
            Ptr <McUeNetDevice> mcuedev = node->GetDevice(j)->GetObject<McUeNetDevice>();
            if (uedev) {
                Vector pos = node->GetObject<MobilityModel>()->GetPosition();
                outFile << "set label \"" << uedev->GetImsi() << "\" at " << pos.x << "," << pos.y
                        << " left font \"Helvetica,8\" textcolor rgb \"black\" front point pt 1 ps "
                           "0.3 lc rgb \"black\" offset 0,0"
                        << std::endl;
            } else if (mmuedev) {
                Vector pos = node->GetObject<MobilityModel>()->GetPosition();
                outFile << "set label \"" << mmuedev->GetImsi() << "\" at " << pos.x << "," << pos.y
                        << " left font \"Helvetica,8\" textcolor rgb \"black\" front point pt 1 ps "
                           "0.3 lc rgb \"black\" offset 0,0"
                        << std::endl;
            } else if (mcuedev) {
                Vector pos = node->GetObject<MobilityModel>()->GetPosition();
                outFile << "set label \"" << mcuedev->GetImsi() << "\" at " << pos.x << "," << pos.y
                        << " left font \"Helvetica,8\" textcolor rgb \"black\" front point pt 1 ps "
                           "0.3 lc rgb \"black\" offset 0,0"
                        << std::endl;
            }
        }
    }
}

void
PrintGnuplottableEnbListToFile(uint64_t m_startTime) {

  //uint64_t m_startTime = (time_now.tv_sec * 1000) + (time_now.tv_usec / 1000);
  uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now().GetMilliSeconds();
  std::string filename1 = "enbs.txt";
  std::string filename2 = "gnbs.txt";

  struct GnbRow
  {
    uint16_t cellId;
    double x;
    double y;
    bool esState;
    double currentEc;
  };

  std::vector<GnbRow> gnbRows;
  curr_total_energy_consumption = 0.0;

  for (NodeList::Iterator it = NodeList::Begin(); it != NodeList::End(); ++it) {
      Ptr <Node> node = *it;
      int nDevs = node->GetNDevices();
      for (int j = 0; j < nDevs; j++) {
          Ptr <LteEnbNetDevice> enbdev = node->GetDevice(j)->GetObject<LteEnbNetDevice>();
          Ptr <MmWaveEnbNetDevice> mmdev = node->GetDevice(j)->GetObject<MmWaveEnbNetDevice>();
          if (enbdev) {
              Vector pos = node->GetObject<MobilityModel>()->GetPosition();
              std::ofstream outFile1;
              outFile1.open(filename1.c_str(), std::ios_base::out | std::ios_base::app);
              if (!outFile1.is_open()) {
                  NS_LOG_ERROR("Can't open file " << filename1);
                  return;
                }
                // Keep the original GUI-compatible enbs.txt format.
                outFile1 << timestamp << "," << enbdev->GetCellId() << "," << pos.x << "," << pos.y << ","
                         << m_startTime << "," << "0" << "," << "30" << std::endl;
                outFile1.close();
            } else if (mmdev) {
                Vector pos = node->GetObject<MobilityModel>()->GetPosition();
                auto ueMap = mmdev->GetUeMap();
                Ptr<MmWaveEnbPhy> enbPhy = node->GetDevice(j)->GetObject<MmWaveEnbNetDevice>()->GetPhy();
                for (const auto &ue: ueMap) {
                    uint64_t imsi_assoc = ue.second->GetImsi();
                    if (IsValidUeIndex(imsi_assoc))
                      {
                        ue_assoc_list[imsi_assoc - 1] = mmdev->GetCellId();
                      }
                }

                uint16_t cell_id = mmdev->GetCellId();
                double es_power = enbPhy->GetTxPower();
                double currentEc = 0.0;
                bool esState = false;

                if (IsValidCellIndex(cell_id))
                  {
                    esState = (es_power == 0);
                    esON_list[cell_id] = esState;
                    currentEc = current_energy_consumption[cell_id];
                    curr_total_energy_consumption += currentEc;
                  }

                gnbRows.push_back({cell_id, pos.x, pos.y, esState, currentEc});
            }
        }
    }

  sum_curr_total_energy_consumption = curr_total_energy_consumption;
  if (max_energy_consumption < curr_total_energy_consumption)
    {
      max_energy_consumption = curr_total_energy_consumption;
    }

  std::ofstream outFile2;
  outFile2.open(filename2.c_str(), std::ios_base::out | std::ios_base::app);
  if (!outFile2.is_open()) {
      NS_LOG_ERROR("Can't open file " << filename2);
      return;
  }

  for (const auto& row : gnbRows)
    {
      outFile2 << timestamp << "," << row.cellId << "," << row.x << "," << row.y << ","
               << m_startTime << "," << row.esState << ","
               << row.currentEc << "," << max_energy_consumption
               << "," << sum_curr_total_energy_consumption << std::endl;
    }
  outFile2.close();
}

void
ClearFile(std::string Filename, uint64_t m_startTime) {
    std::string filename = Filename;
    std::ofstream outFile;
    outFile.open(filename.c_str(), std::ios_base::out | std::ios_base::trunc);
    if (!outFile.is_open()) {
        NS_LOG_ERROR("Can't open file " << filename);
        return;
    }
    outFile.close();
    //  struct timeval time_now{};
    //  gettimeofday (&time_now, nullptr);
    //uint64_t m_startTime = (time_now.tv_sec * 1000) + (time_now.tv_usec / 1000);
    uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now().GetMilliSeconds();
    std::ofstream outFile1;
    outFile1.open(filename.c_str(), std::ios_base::out | std::ios_base::app);

  if (Filename == "ue_position.txt") {
      outFile1 << "timestamp,id,x,y,type,cell,simid" << std::endl;
    }
  else
    {
      outFile1 << "timestamp,id,x,y,simid,ESstate,currEC,maxEC,totalcurrEC" << std::endl;
      // Initial border row used by the GUI map. Keep all columns present so pandas/plotly
      // does not treat power columns as missing.
      outFile1 << timestamp << "," << "0" << "," << maxXAxis << "," << maxYAxis << ","
               << m_startTime << ",0,0,0,0" << std::endl;
    }
    outFile1.close();
}

void
PrintPosition(Ptr<Node> node, int iterator, std::string Filename, uint64_t m_startTime) {

    //uint64_t m_startTime = (time_now.tv_sec * 1000) + (time_now.tv_usec / 1000);
    uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now().GetMilliSeconds();

  int imsi;
  Ptr <Node> node1 = NodeList::GetNode(iterator);
  int nDevs = node->GetNDevices();
  std::string filename = Filename;
  std::ofstream outFile;
  for (int j = 0; j < nDevs; j++) {
      Ptr <McUeNetDevice> mcuedev = node1->GetDevice(j)->GetObject<McUeNetDevice>();
      Ptr <LteUeNetDevice> uedev = node->GetDevice(j)->GetObject<LteUeNetDevice>();
      Ptr <MmWaveUeNetDevice> mmuedev = node->GetDevice(j)->GetObject<MmWaveUeNetDevice>();
      if (mcuedev) {
          imsi = int(mcuedev->GetImsi());
          int serving_cell = IsValidUeIndex(imsi) ? ue_assoc_list[imsi - 1] : 0;
          if (serving_cell==0){
              serving_cell=1;
            }
          Ptr<MobilityModel> model = node->GetObject<MobilityModel> ();
          Vector position = model->GetPosition ();
          NS_LOG_UNCOND ("Position of UE with IMSI " << imsi << " is " << model->GetPosition ()
                                                     << " at time "
                                                     << Simulator::Now ().GetSeconds ()
                                                     << ", UE connected to Cell: " << serving_cell);
            outFile.open(filename.c_str(), std::ios_base::out | std::ios_base::app);
            if (!outFile.is_open()) {
                NS_LOG_ERROR("Can't open file " << filename);
                return;
            }

          outFile << timestamp << "," << imsi << "," << position.x << "," << position.y << ",mc,"
                  << serving_cell << "," << m_startTime << std::endl;
          outFile.close ();
        }
      else
        {
          //
        }
    }
}

void
EnergyConsumptionUpdate (int cellId, std::string filename, double totaloldEnergyConsumption,
                         double totalnewEnergyConsumption)
{
  Time currentTime = Simulator::Now ();
  // The aggregate live series must remain independent of this optional
  // per-cell diagnostic file.
  if (IsValidCellIndex(cellId))
    {
      totalnewEnergyConsumption_storage[cellId] = totalnewEnergyConsumption;
    }

  std::ofstream outFile;
  outFile.open (filename, std::ios_base::out | std::ios_base::app);
  if (!outFile.is_open ())
    {
      NS_LOG_ERROR ("Cannot append energy sample to " << filename);
      return;
    }
  outFile << currentTime.GetSeconds () << "," << totalnewEnergyConsumption << ","
          << (totalnewEnergyConsumption - totaloldEnergyConsumption) << std::endl;
  outFile.close ();
}

void
EnergyConsumptionPrint (int cellId)
{
  if (!IsValidCellIndex(cellId))
    {
      return;
    }

  const double totalNew = totalnewEnergyConsumption_storage[cellId];
  const double totalOld = totaloldEnergyConsumption_storage[cellId];
  const double diff = totalNew - totalOld;

  NS_LOG_UNCOND ("Total energy consumption for mmWave cell "
                 << cellId << ": " << totalNew << "J"
                 << " at time " << Simulator::Now ().GetSeconds ()
                 << ", diff from last measurement is: "
                 << diff << "J");

  current_energy_consumption[cellId] = diff;
  totaloldEnergyConsumption_storage[cellId] = totalNew;
}

bool
ReadTrackedDlFlowQuality (double& packetLossPct, double& meanDelayMs)
{
  packetLossPct = 0.0;
  meanDelayMs = 0.0;
  // Live: packets not received YET, including those still in transit.
  // Final: the same counters after the declared queue-drain period.
  const DlFlowQuality quality = ReadTrackedDlFlowCounters ();
  const uint64_t txPackets = quality.txPackets;
  const uint64_t rxPackets = quality.rxPackets;
  const uint64_t lostPackets = txPackets > rxPackets ? txPackets - rxPackets : 0;
  const double delaySeconds = quality.delaySeconds;

  if (txPackets == 0)
    {
      return false;
    }
  packetLossPct = 100.0 * static_cast<double> (lostPackets) /
                  static_cast<double> (txPackets);
  if (rxPackets == 0)
    {
      // Transmitted packets with no completed receive sample do not have a
      // meaningful average delay yet.  The caller writes an empty CSV field.
      meanDelayMs = -1.0;
    }
  else
    {
      meanDelayMs = 1000.0 * delaySeconds / static_cast<double> (rxPackets);
    }
  return true;
}

void
WriteEeTimeseries ()
{
  const double nowS = Simulator::Now ().GetSeconds ();
  // A terminal sample is scheduled at simTime and also attempted after Run.
  // Keep exactly one row for that instant if both paths are reached.
  if (nowS <= previousEeSampleTime)
    {
      return;
    }
  uint64_t deliveredBytes = 0;
  for (uint32_t appIndex = 1; appIndex < trackedDlSinks.GetN (); ++appIndex)
    {
      Ptr<PacketSink> sink = DynamicCast<PacketSink> (trackedDlSinks.Get (appIndex));
      if (sink)
        {
          deliveredBytes += sink->GetTotalRx ();
        }
    }

  const double totalEnergyJ = ReadTotalRadioEnergyJ ();

  const double deliveredBits = static_cast<double> (deliveredBytes) * 8.0;
  const double intervalS = nowS - previousEeSampleTime;
  const double intervalPowerW = intervalS > 0.0
      ? (totalEnergyJ - previousEeSampleEnergyJ) / intervalS
      : 0.0;
  const double intervalThroughputBps = intervalS > 0.0
      ? (deliveredBits - previousEeSampleDeliveredBits) / intervalS
      : 0.0;
  const double cumulativeEeBitPerJ = totalEnergyJ > 0.0
      ? deliveredBits / totalEnergyJ
      : 0.0;
  double packetLossPct = 0.0;
  double meanDelayMs = 0.0;
  const bool hasFlowQuality =
      ReadTrackedDlFlowQuality (packetLossPct, meanDelayMs);

  std::ofstream outFile;
  outFile.open ("ee_timeseries.csv", std::ios_base::out | std::ios_base::app);
  if (outFile.is_open ())
    {
      outFile << std::setprecision (17) << nowS << "," << deliveredBits << "," << totalEnergyJ << ","
              << intervalPowerW << "," << intervalThroughputBps << ","
              << cumulativeEeBitPerJ << ",";
      if (hasFlowQuality)
        {
          outFile << packetLossPct;
        }
      outFile << ",";
      if (hasFlowQuality && meanDelayMs >= 0.0)
        {
          outFile << meanDelayMs;
        }
      outFile << std::endl;
      outFile.close ();
    }
  else
    {
      NS_LOG_ERROR ("Cannot append live metrics to ee_timeseries.csv");
    }

  previousEeSampleTime = nowS;
  previousEeSampleEnergyJ = totalEnergyJ;
  previousEeSampleDeliveredBits = deliveredBits;
}

static ns3::GlobalValue g_bufferSize("bufferSize", "RLC tx buffer size (MiB)",
                                      ns3::UintegerValue(10),
                                      ns3::MakeUintegerChecker<uint32_t>(0, 4095));

static ns3::GlobalValue g_enableTraces("enableTraces", "If true, generate ns-3 traces",
                                        ns3::BooleanValue(true), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2lteEnabled("e2lteEnabled", "If true, send LTE E2 reports",
                                        ns3::BooleanValue(true), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2nrEnabled("e2nrEnabled", "If true, send NR E2 reports",
                                       ns3::BooleanValue(true), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2du("e2du", "If true, send DU reports", ns3::BooleanValue(true),
                                ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2cuUp("e2cuUp", "If true, send CU-UP reports", ns3::BooleanValue(true),
                                  ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2cuCp("e2cuCp", "If true, send CU-CP reports", ns3::BooleanValue(true),
                                  ns3::MakeBooleanChecker());

static ns3::GlobalValue g_reducedPmValues("reducedPmValues", "If true, use a subset of the the pm containers",
                                          ns3::BooleanValue(false), ns3::MakeBooleanChecker());

static ns3::GlobalValue
    g_hoSinrDifference("hoSinrDifference",
                        "The value for which an handover between MmWave eNB is triggered",
                        ns3::DoubleValue(3), ns3::MakeDoubleChecker<double>());

static ns3::GlobalValue
    g_indicationPeriodicity("indicationPeriodicity",
                             "E2 Indication Periodicity reports (value in seconds)",
                             ns3::DoubleValue(0.1), ns3::MakeDoubleChecker<double>(0.01, 2.0));

static ns3::GlobalValue g_simTime("simTime", "Simulation time in seconds", ns3::DoubleValue(1000),
                                   ns3::MakeDoubleChecker<double>(0.2, 100000.0));

static ns3::GlobalValue g_trafficStart("TrafficStart_s", "UDP traffic start time (s)",
    ns3::DoubleValue(0.1), ns3::MakeDoubleChecker<double>(0.0, 100000.0));
static ns3::GlobalValue g_drainTime("DrainTime_s", "Drain after traffic stops (s); zero selects min(2 s, 20% duration)",
    ns3::DoubleValue(0.0), ns3::MakeDoubleChecker<double>(0.0, 100000.0));

static ns3::GlobalValue g_outageThreshold("outageThreshold",
                                           "SNR threshold for outage events [dB]", // use -1000.0 with NoAuto
                                           ns3::DoubleValue(-5.0),
                                           ns3::MakeDoubleChecker<double>());

static ns3::GlobalValue g_numberOfRaPreambles(
    "numberOfRaPreambles",
    "how many random access preambles are available for the contention based RACH process",
    ns3::UintegerValue(40), // Indicated for TS use case, 52 is default
    ns3::MakeUintegerChecker<uint8_t>(4, 64));

static ns3::GlobalValue
    g_handoverMode("handoverMode",
                    "HO euristic to be used,"
                    "can be only \"NoAuto\", \"FixedTtt\", \"DynamicTtt\",   \"Threshold\"",
                    ns3::StringValue("DynamicTtt"), ns3::MakeStringChecker());

static ns3::GlobalValue g_e2TermIp("e2TermIp", "The IP address of the RIC E2 termination",
                                    ns3::StringValue("127.0.0.1"), ns3::MakeStringChecker());

static ns3::GlobalValue
        g_enableE2FileLogging("enableE2FileLogging",
                              "If true, generate offline file logging instead of connecting to RIC",
                              ns3::BooleanValue(false), ns3::MakeBooleanChecker());
static ns3::GlobalValue g_e2_func_id("KPM_E2functionID", "Function ID to subscribe",
                                      ns3::DoubleValue(2),
                                      ns3::MakeDoubleChecker<double>());
static ns3::GlobalValue g_rc_e2_func_id("RC_E2functionID", "Function ID to subscribe",
                                         ns3::DoubleValue(3),
                                         ns3::MakeDoubleChecker<double>());

static ns3::GlobalValue g_controlFileName("controlFileName",
                                           "The path to the control file (can be absolute)",
                                           ns3::StringValue(""),
                                           ns3::MakeStringChecker());

// TODO: running flags
static ns3::GlobalValue mmWave_nodes ("N_MmWaveEnbNodes", "Number of mmWaveNodes",
                                      ns3::UintegerValue (4),
                                      ns3::MakeUintegerChecker<uint8_t> (1, 255));
// TODO: next step(make it in correct way, regarding to position)
// static ns3::GlobalValue lteEnb_nodes ("N_LteEnbNodes", "Number of LteEnbNodes",
//                                       ns3::UintegerValue (1),
//                                       ns3::MakeUintegerChecker<uint8_t> ());

static ns3::GlobalValue ue_s ("N_Ues", "Number of User Equipments",
                              ns3::UintegerValue (3),
                              ns3::MakeUintegerChecker<uint32_t> (1, MAX_TRACKED_UES));

static ns3::GlobalValue center_freq ("CenterFrequency", "Center Frequency Value",
                                     ns3::DoubleValue (3.5e9),
                                     ns3::MakeDoubleChecker<double> ());

static ns3::GlobalValue bandwidth_value ("Bandwidth", "Bandwidth Value",
                                         ns3::DoubleValue (20e6),
                                         ns3::MakeDoubleChecker<double> ());
// TODO: check for later
// static ns3::GlobalValue num_antennas_McUe ("N_AntennasMcUe", "Number of Antenna as McUe",
//                                       ns3::IntegerValue (1),
//                                       ns3::MakeIntegerChecker<int> ());

// static ns3::GlobalValue num_antennas_MmWave ("N_AntennasMmWave", "Number of Antenna as MmWave",
//                                       ns3::IntegerValue (1),
//                                       ns3::MakeIntegerChecker<int> ());

// 0 preserves the original centre-and-ring example; 1 gives a regular polygon.
static ns3::GlobalValue cell_layout("CellLayout", "0: centre and ring, 1: regular polygon of separate NR sites",
                                    ns3::UintegerValue(1), ns3::MakeUintegerChecker<uint32_t>(0, 1));

static ns3::GlobalValue interside_distance_value_ue ("IntersideDistanceUEs", "Interside Distance Value",
                                      ns3::DoubleValue (500),
                                      ns3::MakeDoubleChecker<double> ());
static ns3::GlobalValue interside_distance_value_cell ("IntersideDistanceCells", "Interside Distance Value",
                                                  ns3::DoubleValue (600),
                                                  ns3::MakeDoubleChecker<double> ());

static ns3::GlobalValue g_udpPacketSizeBytes("UdpPacketSizeBytes",
                                             "UDP DL packet size used by every UE",
                                             ns3::UintegerValue(200),
                                             ns3::MakeUintegerChecker<uint32_t>(12, 65507));

static ns3::GlobalValue g_udpIntervalUs("UdpIntervalUs",
                                        "UDP DL interval per UE in microseconds",
                                        ns3::UintegerValue(500),
                                        ns3::MakeUintegerChecker<uint32_t>(1));

int
main(int argc, char *argv[]) {
  LogComponentEnableAll(LOG_PREFIX_ALL);
  //  LogComponentEnable ("RicControlMessage", LOG_LEVEL_ALL);
  //  LogComponentEnable ("KpmIndication", LOG_LEVEL_DEBUG);
  //LogComponentEnable("KpmIndication", LOG_LEVEL_INFO);

  // LogComponentEnable ("Asn1Types", LOG_LEVEL_LOGIC);
  //   LogComponentEnable ("E2Termination", LOG_LEVEL_LOGIC);
  //  LogComponentEnable ("E2Termination", LOG_LEVEL_DEBUG);

  // LogComponentEnable ("LteEnbNetDevice", LOG_LEVEL_ALL);
  // LogComponentEnable ("MmWaveEnbNetDevice", LOG_LEVEL_INFO);
 //  LogComponentEnable ("LteEnbRrc", LOG_LEVEL_INFO);
 //LogComponentEnable ("EpcX2", LOG_LEVEL_LOGIC);

  

  // The maximum X coordinate of the scenario


  maxXAxis = 4000;
  // The maximum Y coordinate of the scenario
  maxYAxis = 4000;

  // Command line arguments
  CommandLine cmd;
  cmd.Parse(argc, argv);

  bool harqEnabled = true;

  UintegerValue uintegerValue;
  BooleanValue booleanValue;
  StringValue stringValue;
  DoubleValue doubleValue;

  GlobalValue::GetValueByName("hoSinrDifference", doubleValue);
  double hoSinrDifference = doubleValue.Get();
  GlobalValue::GetValueByName("bufferSize", uintegerValue);
  uint32_t bufferSize = uintegerValue.Get();
  GlobalValue::GetValueByName("enableTraces", booleanValue);
  bool enableTraces = booleanValue.Get();
  GlobalValue::GetValueByName("outageThreshold", doubleValue);
  double outageThreshold = doubleValue.Get();
  GlobalValue::GetValueByName("handoverMode", stringValue);
  std::string handoverMode = stringValue.Get();
  GlobalValue::GetValueByName("e2TermIp", stringValue);
  std::string e2TermIp = stringValue.Get();
  GlobalValue::GetValueByName("enableE2FileLogging", booleanValue);
  bool enableE2FileLogging = booleanValue.Get();
  GlobalValue::GetValueByName("KPM_E2functionID", doubleValue);
  double g_e2_func_id = doubleValue.Get();
  GlobalValue::GetValueByName("RC_E2functionID", doubleValue);
  double g_rc_e2_func_id = doubleValue.Get();


  GlobalValue::GetValueByName("numberOfRaPreambles", uintegerValue);
  uint8_t numberOfRaPreambles = uintegerValue.Get();

    NS_LOG_UNCOND("bufferSize " << bufferSize << " OutageThreshold " << outageThreshold
                                << " HandoverMode " << handoverMode << " e2TermIp " << e2TermIp
                                << " enableE2FileLogging " << enableE2FileLogging
                                << " E2 Function ID " << g_e2_func_id);

  GlobalValue::GetValueByName("e2lteEnabled", booleanValue);
  bool e2lteEnabled = booleanValue.Get();
  GlobalValue::GetValueByName("e2nrEnabled", booleanValue);
  bool e2nrEnabled = booleanValue.Get();
  GlobalValue::GetValueByName("e2du", booleanValue);
  bool e2du = booleanValue.Get();
  GlobalValue::GetValueByName("e2cuUp", booleanValue);
  bool e2cuUp = booleanValue.Get();
  GlobalValue::GetValueByName("e2cuCp", booleanValue);
  bool e2cuCp = booleanValue.Get();

  GlobalValue::GetValueByName("reducedPmValues", booleanValue);
  bool reducedPmValues = booleanValue.Get();

  GlobalValue::GetValueByName("indicationPeriodicity", doubleValue);
  double indicationPeriodicity = doubleValue.Get();
  GlobalValue::GetValueByName("controlFileName", stringValue);
  std::string controlFilename = stringValue.Get();

  NS_LOG_UNCOND("e2lteEnabled " << e2lteEnabled << " e2nrEnabled " << e2nrEnabled << " e2du "
                                 << e2du << " e2cuCp " << e2cuCp << " e2cuUp " << e2cuUp
                                 << " controlFilename " << controlFilename
                                 << " indicationPeriodicity " << indicationPeriodicity);

  Config::SetDefault("ns3::LteEnbNetDevice::ControlFileName", StringValue(controlFilename));
  Config::SetDefault("ns3::LteEnbNetDevice::E2Periodicity", DoubleValue(indicationPeriodicity));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::E2Periodicity",
                      DoubleValue(indicationPeriodicity));

  Config::SetDefault("ns3::MmWaveHelper::E2ModeLte", BooleanValue(e2lteEnabled));
  Config::SetDefault("ns3::MmWaveHelper::E2ModeNr", BooleanValue(e2nrEnabled));

  // The DU PM reports should come from both NR gNB as well as LTE eNB,
  // since in the RLC/MAC/PHY entities are present in BOTH NR gNB as well as LTE eNB.
  // DU reports from LTE eNB are not implemented in this release
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableDuReport", BooleanValue(e2du));

  // The CU-UP PM reports should only come from LTE eNB, since in the NS3 “EN-DC
  // simulation (Option 3A)”, the PDCP is only in the LTE eNB and NOT in the NR gNB
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableCuUpReport", BooleanValue(e2cuUp));
  Config::SetDefault("ns3::LteEnbNetDevice::EnableCuUpReport", BooleanValue(e2cuUp));

  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableCuCpReport", BooleanValue(e2cuCp));
  Config::SetDefault("ns3::LteEnbNetDevice::EnableCuCpReport", BooleanValue(e2cuCp));

  Config::SetDefault("ns3::MmWaveEnbNetDevice::ReducedPmValues", BooleanValue(reducedPmValues));
  Config::SetDefault("ns3::LteEnbNetDevice::ReducedPmValues", BooleanValue(reducedPmValues));

  Config::SetDefault("ns3::LteEnbNetDevice::EnableE2FileLogging",
                      BooleanValue(enableE2FileLogging));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableE2FileLogging",
                      BooleanValue(enableE2FileLogging));


  Config::SetDefault("ns3::LteEnbNetDevice::KPM_E2functionID",
                      DoubleValue(g_e2_func_id));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::KPM_E2functionID",
                      DoubleValue(g_e2_func_id));

  Config::SetDefault("ns3::LteEnbNetDevice::RC_E2functionID",
                      DoubleValue(g_rc_e2_func_id));

  Config::SetDefault("ns3::MmWaveEnbMac::NumberOfRaPreambles",
                      UintegerValue(numberOfRaPreambles));

  Config::SetDefault("ns3::MmWaveHelper::HarqEnabled", BooleanValue(harqEnabled));
  Config::SetDefault("ns3::MmWaveHelper::UseIdealRrc", BooleanValue(true));
  Config::SetDefault("ns3::MmWaveHelper::E2TermIp", StringValue(e2TermIp));

  Config::SetDefault("ns3::MmWaveFlexTtiMacScheduler::HarqEnabled", BooleanValue(harqEnabled));
  Config::SetDefault("ns3::MmWavePhyMacCommon::NumHarqProcess", UintegerValue(100));
  //Config::SetDefault ("ns3::MmWaveBearerStatsCalculator::EpochDuration", TimeValue (MilliSeconds (10.0)));

  // set to false to use the 3GPP radiation pattern (proper configuration of the bearing and downtilt angles is needed)
  Config::SetDefault("ns3::PhasedArrayModel::AntennaElement",
    PointerValue(CreateObject<IsotropicAntennaModel>()));
Config::SetDefault ("ns3::ThreeGppChannelModel::UpdatePeriod", TimeValue (MilliSeconds (100.0)));
Config::SetDefault ("ns3::ThreeGppChannelConditionModel::UpdatePeriod",
  TimeValue (MilliSeconds (100)));

  Config::SetDefault("ns3::LteRlcAm::ReportBufferStatusTimer", TimeValue(MilliSeconds(10.0)));
  Config::SetDefault("ns3::LteRlcUmLowLat::ReportBufferStatusTimer",
                      TimeValue(MilliSeconds(10.0)));
  Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(bufferSize * 1024 * 1024));
  Config::SetDefault("ns3::LteRlcUmLowLat::MaxTxBufferSize",
                      UintegerValue(bufferSize * 1024 * 1024));
  Config::SetDefault("ns3::LteRlcAm::MaxTxBufferSize", UintegerValue(bufferSize * 1024 * 1024));

  Config::SetDefault("ns3::LteEnbRrc::OutageThreshold", DoubleValue(outageThreshold));
  Config::SetDefault("ns3::LteEnbRrc::SecondaryCellHandoverMode", StringValue(handoverMode));
  Config::SetDefault("ns3::LteEnbRrc::HoSinrDifference", DoubleValue(hoSinrDifference));
  Config::SetDefault("ns3::ThreeGppPropagationLossModel::ShadowingEnabled", BooleanValue(false));

  GlobalValue::GetValueByName("Bandwidth", doubleValue);
  double bandwidth = doubleValue.Get();
  GlobalValue::GetValueByName("CenterFrequency", doubleValue);
  double centerFrequency = doubleValue.Get();
  GlobalValue::GetValueByName("IntersideDistanceUEs", doubleValue);
  double isd_ue = doubleValue.Get();
  GlobalValue::GetValueByName("IntersideDistanceCells", doubleValue);
  double isd_cell = doubleValue.Get();
  Config::SetDefault("ns3::ThreeGppPropagationLossModel::Frequency", DoubleValue(centerFrequency));

  // Number of antennas in each UE
  // GlobalValue::GetValueByName ("N_AntennasMcUe", uintegerValue);
  int numAntennasMcUe = 1; //uintegerValue.Get();
  // Number of antennas in each mmWave BS
  // GlobalValue::GetValueByName ("N_AntennasMmWave", uintegerValue);
  int numAntennasMmWave = 1; //uintegerValue.Get();

  NS_LOG_INFO("Bandwidth " << bandwidth << " centerFrequency " << double(centerFrequency)
                            << " isd_ue " << isd_ue << " numAntennasMcUe " << numAntennasMcUe
                            << " numAntennasMmWave " << numAntennasMmWave);

  // Set the number of antennas in the devices
  Config::SetDefault("ns3::McUeNetDevice::AntennaNum", UintegerValue(numAntennasMcUe));
  Config::SetDefault("ns3::MmWaveNetDevice::AntennaNum", UintegerValue(numAntennasMmWave));
  Config::SetDefault("ns3::MmWavePhyMacCommon::Bandwidth", DoubleValue(bandwidth));
  Config::SetDefault("ns3::MmWavePhyMacCommon::CenterFreq", DoubleValue(centerFrequency));

  Ptr <MmWaveHelper> mmwaveHelper = CreateObject<MmWaveHelper>();
  mmwaveHelper->SetPathlossModelType("ns3::ThreeGppUmiStreetCanyonPropagationLossModel");
  mmwaveHelper->SetChannelConditionModelType("ns3::ThreeGppUmiStreetCanyonChannelConditionModel");

  Ptr <MmWavePointToPointEpcHelper> epcHelper = CreateObject<MmWavePointToPointEpcHelper>();
  mmwaveHelper->SetEpcHelper(epcHelper);

  GlobalValue::GetValueByName("N_MmWaveEnbNodes", uintegerValue);
  uint8_t nMmWaveEnbNodes = static_cast<uint8_t>(uintegerValue.Get());
  // GlobalValue::GetValueByName ("N_LteEnbNodes", uintegerValue);
  uint8_t nLteEnbNodes = 1; //uintegerValue.Get();
  GlobalValue::GetValueByName("N_Ues", uintegerValue);
  uint32_t nUeNodes = uintegerValue.Get();
  NS_LOG_INFO(" Bandwidth " << bandwidth << " centerFrequency " << double(centerFrequency)
                             << " isd_cell " << isd_cell << " numAntennasMcUe " << numAntennasMcUe
                             << " numAntennasMmWave " << numAntennasMmWave << " nMmWaveEnbNodes "
                             << unsigned(nMmWaveEnbNodes));

  // Get SGW/PGW and create a single RemoteHost
  Ptr <Node> pgw = epcHelper->GetPgwNode();
  NodeContainer remoteHostContainer;
  remoteHostContainer.Create(1);
  Ptr <Node> remoteHost = remoteHostContainer.Get(0);
  InternetStackHelper internet;
  internet.Install(remoteHostContainer);

  // Create the Internet by connecting remoteHost to pgw. Setup routing too
  PointToPointHelper p2ph;
  p2ph.SetDeviceAttribute("DataRate", DataRateValue(DataRate("100Gb/s")));
  p2ph.SetDeviceAttribute("Mtu", UintegerValue(2500));
  p2ph.SetChannelAttribute("Delay", TimeValue(Seconds(0.010)));
  NetDeviceContainer internetDevices = p2ph.Install(pgw, remoteHost);
  Ipv4AddressHelper ipv4h;
  ipv4h.SetBase("1.0.0.0", "255.0.0.0");
  Ipv4InterfaceContainer internetIpIfaces = ipv4h.Assign(internetDevices);
  // interface 0 is localhost, 1 is the p2p device
  Ipv4Address remoteHostAddr = internetIpIfaces.GetAddress(1);
  Ipv4StaticRoutingHelper ipv4RoutingHelper;
  Ptr <Ipv4StaticRouting> remoteHostStaticRouting =
      ipv4RoutingHelper.GetStaticRouting(remoteHost->GetObject<Ipv4>());
  remoteHostStaticRouting->AddNetworkRouteTo(Ipv4Address("7.0.0.0"), Ipv4Mask("255.0.0.0"), 1);

  // create LTE, mmWave eNB nodes and UE node
  NodeContainer ueNodes;
  NodeContainer mmWaveEnbNodes;
  NodeContainer lteEnbNodes;
  NodeContainer allEnbNodes;
  mmWaveEnbNodes.Create(nMmWaveEnbNodes);
  lteEnbNodes.Create(nLteEnbNodes);
  ueNodes.Create(nUeNodes);
  allEnbNodes.Add(lteEnbNodes);
  allEnbNodes.Add(mmWaveEnbNodes);

  NodeContainerManager::GetInstance().SetMmWaveEnbNodes(mmWaveEnbNodes);

  // Position
  Vector centerPosition = Vector(maxXAxis / 2, maxYAxis / 2, 3);

  // Install Mobility Model
  Ptr <ListPositionAllocator> enbPositionAlloc = CreateObject<ListPositionAllocator>();

  // One LTE anchor plus separate NR sites. These are not sectors of one mast.
  centerPosition.z = 10.0;
  enbPositionAlloc->Add(centerPosition);
  GlobalValue::GetValueByName("CellLayout", uintegerValue);
  const bool polygon = uintegerValue.Get() == 1;
  if (!polygon || nMmWaveEnbNodes == 1)
    enbPositionAlloc->Add(centerPosition);
  const uint32_t ringCount = polygon && nMmWaveEnbNodes > 1 ? nMmWaveEnbNodes : nMmWaveEnbNodes - 1;
  // In the polygon variant isd_cell is the distance between adjacent sites.
  const double radius = polygon && ringCount > 1 ? isd_cell / (2.0 * sin(M_PI / ringCount)) : isd_cell;
  for (uint32_t i = 0; i < ringCount; ++i) {
    const double angle = 2 * M_PI * i / ringCount;
    enbPositionAlloc->Add(Vector(centerPosition.x + radius * cos(angle),
                                centerPosition.y + radius * sin(angle), 10.0));
  }

  MobilityHelper enbmobility;
  enbmobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  enbmobility.SetPositionAllocator(enbPositionAlloc);
  enbmobility.Install(allEnbNodes);

  MobilityHelper uemobility;

  Ptr <UniformDiscPositionAllocator> uePositionAlloc = CreateObject<UniformDiscPositionAllocator>();

  uePositionAlloc->SetX(centerPosition.x);
  uePositionAlloc->SetY(centerPosition.y);
  uePositionAlloc->SetRho(isd_ue);
  uePositionAlloc->SetZ(1.5);
  Ptr <UniformRandomVariable> speed = CreateObject<UniformRandomVariable>();
  speed->SetAttribute("Min", DoubleValue(2.0));
  speed->SetAttribute("Max", DoubleValue(4.0));

  uemobility.SetMobilityModel("ns3::RandomWalk2dOutdoorMobilityModel", "Speed",
                               PointerValue(speed), "Bounds",
                               RectangleValue(Rectangle(0, maxXAxis, 0, maxYAxis)));
  uemobility.SetPositionAllocator(uePositionAlloc);
  uemobility.Install(ueNodes);

  // Install mmWave, lte, mc Devices to the nodesnumAntennasMmWave
  NetDeviceContainer lteEnbDevs = mmwaveHelper->InstallLteEnbDevice(lteEnbNodes);
  NetDeviceContainer mmWaveEnbDevs = mmwaveHelper->InstallEnbDevice(mmWaveEnbNodes);
  NetDeviceContainer mcUeDevs = mmwaveHelper->InstallMcUeDevice(ueNodes);

  // Install the IP stack on the UEs
  internet.Install(ueNodes);
  Ipv4InterfaceContainer ueIpIface;
  ueIpIface = epcHelper->AssignUeIpv4Address(NetDeviceContainer(mcUeDevs));
  // Assign IP address to UEs, and install applications
  for (uint32_t u = 0; u < ueNodes.GetN(); ++u) {
      Ptr <Node> ueNode = ueNodes.Get(u);
      // Set the default gateway for the UE
      Ptr <Ipv4StaticRouting> ueStaticRouting =
          ipv4RoutingHelper.GetStaticRouting(ueNode->GetObject<Ipv4>());
      ueStaticRouting->SetDefaultRoute(epcHelper->GetUeDefaultGatewayAddress(), 1);
    }

  // Add X2 interfaces
  mmwaveHelper->AddX2Interface(lteEnbNodes, mmWaveEnbNodes);

 // for (uint16_t i = 0; i < mmWaveEnbNodes.GetN(); ++i)
//{
 // for (uint16_t j = i+1; j < mmWaveEnbNodes.GetN(); ++j) 
  //{
   // if (i != j)
    //{
     // mmwaveHelper->AddX2Interface(mmWaveEnbNodes.Get(i), mmWaveEnbNodes.Get(j));
    //}
  //}
//}

  // Manual attachment
  mmwaveHelper->AttachToClosestEnb(mcUeDevs, mmWaveEnbDevs, lteEnbDevs);

  BasicEnergySourceHelper basicEnergySourceHelper;
  basicEnergySourceHelper.Set ("BasicEnergySourceInitialEnergyJ", DoubleValue (1000000000000));
  basicEnergySourceHelper.Set ("BasicEnergySupplyVoltageV", DoubleValue (5.0));
  energy::EnergySourceContainer sources = basicEnergySourceHelper.Install (mmWaveEnbNodes);
  MmWaveRadioEnergyModelEnbHelper nrEnbHelper;

  energy::DeviceEnergyModelContainer deviceEModel = nrEnbHelper.Install (mmWaveEnbDevs, sources);
  trackedEnergyModels = deviceEModel;

  GlobalValue::GetValueByName ("simTime", doubleValue);
  double simTime = doubleValue.Get ();
  GlobalValue::GetValueByName ("TrafficStart_s", doubleValue);
  const double trafficStartS = doubleValue.Get ();
  GlobalValue::GetValueByName ("DrainTime_s", doubleValue);
  const double drainTimeS = doubleValue.Get () > 0.0
      ? doubleValue.Get () : std::min (2.0, 0.2 * simTime);
  const double trafficStopS = simTime - drainTimeS;
  NS_ABORT_MSG_IF (trafficStartS >= trafficStopS,
                   "TrafficStart_s + DrainTime_s must be less than simTime");
  int numPrints = std::max (1, static_cast<int> (std::ceil (simTime / 0.1)));

  for (int x = 0; x < nMmWaveEnbNodes; ++x)
    {
      Ptr<MmWaveEnbNetDevice> mmWaveDev = mmWaveEnbDevs.Get(x)->GetObject<MmWaveEnbNetDevice>();
      uint16_t cellId = mmWaveDev ? mmWaveDev->GetCellId() : static_cast<uint16_t>(x + 2);

      std::ostringstream energyFileName;
      energyFileName << "energyfilecell" << cellId << ".csv";

      std::ofstream outFile;
      outFile.open (energyFileName.str (), std::ios_base::out | std::ios_base::trunc);
      if (!outFile.is_open ())
        {
          NS_LOG_ERROR ("Cannot create " << energyFileName.str ());
          return 1;
        }
      outFile << "Time,NetEnergy,DiffEnergy" << std::endl;
      // The trace callback opens this path independently.  Closing the
      // truncating stream now avoids a buffered header being flushed over
      // callback rows only when the simulation ends.
      outFile.close ();
    }

  for (int x = 0; x < nMmWaveEnbNodes; ++x)
    {
      Ptr<MmWaveEnbNetDevice> mmWaveDev = mmWaveEnbDevs.Get(x)->GetObject<MmWaveEnbNetDevice>();
      uint16_t cellId = mmWaveDev ? mmWaveDev->GetCellId() : static_cast<uint16_t>(x + 2);

      std::ostringstream filename;
      filename << "energyfilecell" << cellId << ".csv";
      deviceEModel.Get (x)->TraceConnectWithoutContext (
          "TotalEnergyConsumption",
          MakeBoundCallback (&EnergyConsumptionUpdate, cellId, filename.str ()));
      for (int i = 0; i < numPrints; i++)
        {
          Simulator::Schedule (Seconds (i * simTime / numPrints), &EnergyConsumptionPrint, cellId);
        }
    }

  // Install and start applications
  // On the remoteHost there is UDP OnOff Application

  uint16_t portUdp = 60000;
  Address sinkLocalAddressUdp(InetSocketAddress(Ipv4Address::GetAny(), portUdp));
  PacketSinkHelper sinkHelperUdp("ns3::UdpSocketFactory", sinkLocalAddressUdp);
  AddressValue serverAddressUdp(InetSocketAddress(remoteHostAddr, portUdp));

  ApplicationContainer sinkApp;
  sinkApp.Add(sinkHelperUdp.Install(remoteHost));

  ApplicationContainer clientApp;

  for (uint32_t u = 0; u < ueNodes.GetN(); ++u) {
      // Full traffic
      PacketSinkHelper dlPacketSinkHelper("ns3::UdpSocketFactory",
                                           InetSocketAddress(Ipv4Address::GetAny(), 1234));
      ApplicationContainer ueSink = dlPacketSinkHelper.Install(ueNodes.Get(u));
      ueSink.Get (0)->TraceConnectWithoutContext ("Rx", MakeCallback (&TrackDlReceive));
      sinkApp.Add(ueSink);
      UdpClientHelper dlClient(ueIpIface.GetAddress(u), 1234);
      GlobalValue::GetValueByName("UdpIntervalUs", uintegerValue);
      uint32_t udpIntervalUs = uintegerValue.Get();
      GlobalValue::GetValueByName("UdpPacketSizeBytes", uintegerValue);
      uint32_t udpPacketSizeBytes = uintegerValue.Get();
      trackedUdpPacketSize = udpPacketSizeBytes;
      dlClient.SetAttribute("Interval", TimeValue(MicroSeconds(udpIntervalUs)));
      dlClient.SetAttribute("MaxPackets", UintegerValue(UINT32_MAX));
      dlClient.SetAttribute("PacketSize", UintegerValue(udpPacketSizeBytes)); // default 200 in Orange scenario
      clientApp.Add(dlClient.Install(remoteHost));
    }

  // Start applications

  trackedDlSinks = sinkApp;
  trackedDlClients = clientApp;
  trackedDlRxPackets = 0;
  trackedDlDelaySeconds = 0.0;
  previousEeSampleTime = 0.0;
  previousEeSampleEnergyJ = 0.0;
  previousEeSampleDeliveredBits = 0.0;
  lastRadioEnergyReadTime = -1.0;
  lastRadioEnergyReadValueJ = 0.0;
  {
    std::ofstream eeSeriesFile;
    eeSeriesFile.open ("ee_timeseries.csv", std::ios_base::out | std::ios_base::trunc);
    if (!eeSeriesFile.is_open ())
      {
        NS_LOG_ERROR ("Cannot create live metrics file ee_timeseries.csv");
        return 1;
      }
    eeSeriesFile << "Time_s,DeliveredBits,TotalEnergy_J,IntervalPower_W,IntervalThroughput_bps,CumulativeEE_bit_per_J,PacketLoss_pct,MeanDelay_ms" << std::endl;
    eeSeriesFile.close ();
  }
  for (int i = 1; i < numPrints; ++i)
    {
      // Schedule just after the cell energy samples at the same nominal instant.
      Simulator::Schedule (Seconds (i * simTime / numPrints + 0.000001), &WriteEeTimeseries);
    }
  // Queue timing consumes the last live row, so schedule an exact endpoint
  // before Simulator::Stop installs its stop event at this same instant.
  Simulator::Schedule (Seconds (simTime), &WriteEeTimeseries);

  sinkApp.Start (Seconds (0));

  clientApp.Start(Seconds(trafficStartS));
  clientApp.Stop(Seconds(trafficStopS));

  struct timeval time_now{};
  gettimeofday(&time_now, nullptr);
  uint64_t t_startTime_simid = (time_now.tv_sec * 1000) + (time_now.tv_usec / 1000);
  std::string ue_poss_out = "ue_position.txt";
  ClearFile(ue_poss_out, t_startTime_simid);
  ClearFile("enbs.txt", t_startTime_simid);
  ClearFile("gnbs.txt", t_startTime_simid);
  // Since nodes are randomly allocated during each run we always need to print their positions
  PrintGnuplottableUeListToFile("ues.txt");

  int nodecount = int(NodeList::GetNNodes());
  // NS_LOG_UNCOND ("number of nodes: " << nodecount);
  int UE_iterator = nodecount - int (nUeNodes);

  for (int i = 1; i < numPrints; i++) {
      Simulator::Schedule(Seconds(i * simTime / numPrints), &PrintGnuplottableEnbListToFile, t_startTime_simid);
      for (uint32_t j = 0; j < ueNodes.GetN(); j++) {
          Simulator::Schedule(Seconds(i * simTime / numPrints), &PrintPosition, ueNodes.Get(j),
                               j + UE_iterator, ue_poss_out, t_startTime_simid);
        }
    }
  // Keep the last map frame aligned with the exact modeled duration.  The
  // gNB callback is inserted first so its serving-cell information is ready
  // for the UE rows at the same instant.
  Simulator::Schedule(Seconds(simTime), &PrintGnuplottableEnbListToFile, t_startTime_simid);
  for (uint32_t j = 0; j < ueNodes.GetN(); j++) {
      Simulator::Schedule(Seconds(simTime), &PrintPosition, ueNodes.Get(j),
                           j + UE_iterator, ue_poss_out, t_startTime_simid);
    }

  if (enableTraces) {
      mmwaveHelper->EnableTraces();
    }

  // trick to enable PHY traces for the LTE stack
  Ptr <LteHelper> lteHelper = CreateObject<LteHelper>();
  lteHelper->Initialize();
  lteHelper->EnablePhyTraces();
  lteHelper->EnableMacTraces();

  // Since nodes are randomly allocated during each run we always need to print their positions
  //PrintGnuplottableUeListToFile ("ues.txt");
  // PrintGnuplottableEnbListToFile ("enbs.txt");

  bool run = true;
  if (run) {
      NS_LOG_UNCOND("Simulation time is " << simTime << " seconds ");
      Simulator::Stop(Seconds(simTime));
      NS_LOG_INFO("Run Simulation.");
      Simulator::Run();

      // The periodic schedule intentionally stops just before simTime.  Add
      // one final, durable sample so a live reader and queue calibration see
      // the exact modeled duration rather than one sampling interval less.
      WriteEeTimeseries ();

      uint64_t deliveredBytes = 0;
      for (uint32_t appIndex = 1; appIndex < sinkApp.GetN(); ++appIndex)
        {
          Ptr<PacketSink> sink = DynamicCast<PacketSink>(sinkApp.Get(appIndex));
          if (sink)
            {
              deliveredBytes += sink->GetTotalRx();
            }
        }

      const double totalEnergyJ = ReadTotalRadioEnergyJ ();
      const DlFlowQuality quality = ReadTrackedDlFlowCounters ();
      // A finite-window delivery failure ratio, including packets still queued
      // after the declared drain. This is distinct from live monitor loss.
      const uint64_t undeliveredPackets = quality.txPackets > quality.rxPackets
          ? quality.txPackets - quality.rxPackets : 0;
      const bool countersValid = quality.rxPackets <= quality.txPackets;
      const double deliveredBits = static_cast<double>(deliveredBytes) * 8.0;

      std::ofstream summaryFile;
      summaryFile.open("ee_summary.csv", std::ios_base::out | std::ios_base::trunc);
      if (!summaryFile.is_open ())
        {
          NS_LOG_ERROR ("Cannot write final ee_summary.csv");
          Simulator::Destroy ();
          return 1;
        }
      summaryFile << "simTime_s,nMmWaveCells,nUes,deliveredData_bits,totalEnergy_J,EE_bit_per_J,measurement_start_s,measurement_duration_s,traffic_start_s,traffic_stop_s,drain_duration_s,tx_packets,rx_packets,lost_packets,delay_sum_s,packet_loss_pct,mean_delay_ms,avg_throughput_mbps,energy_scope,data_layer,packet_loss_definition,simulator_source,qos_source" << std::endl;
      summaryFile << std::setprecision (17)
                  << simTime << "," << unsigned(nMmWaveEnbNodes) << "," << nUeNodes << ","
                  << deliveredBits << "," << totalEnergyJ << ",";
      if (totalEnergyJ > 0.0)
        {
          summaryFile << deliveredBits / totalEnergyJ;
        }
      else
        {
          summaryFile << "N/A";
        }
      summaryFile << ",0,"
                  << simTime << "," << trafficStartS << "," << trafficStopS << ","
                  << drainTimeS << "," << quality.txPackets << "," << quality.rxPackets << ","
                  ;
      if (countersValid)
        {
          summaryFile << undeliveredPackets;
        }
      else
        {
          summaryFile << "INVALID";
        }
      summaryFile << "," << quality.delaySeconds << ",";
      if (countersValid && quality.txPackets > 0)
        {
          summaryFile << 100.0 * undeliveredPackets / quality.txPackets;
        }
      summaryFile << ",";
      if (quality.rxPackets > 0)
        {
          summaryFile << 1000.0 * quality.delaySeconds / quality.rxPackets;
        }
      summaryFile << "," << deliveredBits / simTime / 1e6
                  << ",nr_radio_model,udp_application_payload,not_received_by_measurement_end,mmwave_LENA_oran_custom,udp_application_seq_ts"
                  << std::endl;
      summaryFile.close();
    }

  NS_LOG_INFO(lteHelper);
  Simulator::Destroy();
  NS_LOG_INFO("Done.");
  return 0;
}

// editor test
