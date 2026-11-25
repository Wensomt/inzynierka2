/*
 * Licensed to the OpenAirInterface (OAI) Software Alliance under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The OpenAirInterface Software Alliance licenses this file to You under
 * the OAI Public License, Version 1.1  (the "License"); you may not use this file
 * except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.openairinterface.org/?page_id=698
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *-------------------------------------------------------------------------------
 * For more information about the OpenAirInterface (OAI) Software Alliance:
 *      contact@openairinterface.org
 *-------------------------------------------------------------------------------
 * Authors:
 *  Mina Yonan <m.yonan@aucegypt.edu>
 *  Mostafa Ashraf <mostafa.ashraf.ext@orange.com>
 *  Abdelrhman Soliman <abdelrhman.soliman.ext@orange.com>
 *  Aya Kamal <aya.kamal.ext@orange.com>
 */
// RIC-TaaP xApps
// Zero-Touch ES with cell utilization xApp

#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_struct.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_list.h"
#include "../../../../src/util/alg_ds/ds/lock_guard/lock_guard.h"
#include "../../../../src/sm/rc_sm/rc_sm_id.h"
#include "../../../../src/sm/rc_sm/ie/rc_data_ie.h"
#include "../../../../src/util/e.h"

#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <unistd.h>

/*
 * Engineering-grade energy-saving policy for laboratory simulations.
 *
 * The original Orange xApp is a useful O-RAN demonstrator, but it is too
 * aggressive for repeatable EE simulations. This version adds:
 *  - a deterministic policy configuration,
 *  - minimum low-load report requirement,
 *  - target-cell quality checks,
 *  - target-cell capacity guard,
 *  - cooldown/backoff to avoid repeated power cycling,
 *  - CSV audit log of every decision.
 *
 * Important limitation: in the KPM report style used by this Orange demo, PRB
 * counters are not reliably mapped to a concrete serving cell structure inside
 * this xApp.  Therefore the decision guard uses UE count + serving/neighbor
 * SINR.  PRB/throughput/energy are still evaluated in the scenario/GUI.
 */
#define EE_DEFAULT_MIN_TARGET_SINR_DB 0.0
#define EE_DEFAULT_MIN_HANDOVER_GAIN_DB -3.0
#define EE_DEFAULT_MAX_UES_FOR_SHUTDOWN 1
#define EE_DEFAULT_MAX_UES_ON_TARGET 64
#define EE_DEFAULT_REQUIRED_STABLE_LOW_LOAD_REPORTS 3
#define EE_DEFAULT_REQUIRED_NEIGHBOR_SAMPLES 3
#define EE_DEFAULT_SHUTDOWN_CONFIRMATION_SEC 5
#define EE_DEFAULT_SHUTDOWN_TIMEOUT_SEC 30
#define EE_DEFAULT_CELL_SWITCH_COOLDOWN_SEC 60
#define EE_DEFAULT_PROTECTED_CELL_ID 0
#define EE_DEFAULT_DECISION_PERIOD_SEC 1
#define EE_DEFAULT_WAIT_FOR_E2_SEC 30

typedef struct {
    const e2_node_arr_xapp_t* nodes;
    struct SINRNeighboringValues* neighCells;
    int ueID;
    uint16_t frmCurntCell;
    uint16_t toTargetCell;
    double servingSinr;
    double targetSinr;
} callback_data_t;

typedef uint16_t (*Callback)(callback_data_t);

typedef enum {
    CONNECTED_MODE_MOBILITY = 3,
    ENERGY_STATE = 300
} rc_ctrl_service_style_id_e;
  
typedef enum {
    CELL_OFF = '0',
    CELL_ON = '1',  
    CELL_SLEEP = '2',
} cell_state_e;
  
typedef enum {
    QOS_FLOW_ID_1 = 1,
    QOS_FLOW_ID_10 = 10
} qos_flow_id_e;

typedef enum {
    DRB_ID_3 = 3
} drb_id_e;
  
typedef enum {
    PDU_SESSION_ID_5 = 5
} pdu_session_id_e;
  

static uint64_t const period_ms = 100;

static pthread_mutex_t mtx;

typedef struct {
  double min_target_sinr_db;
  double min_handover_gain_db;
  int max_ues_for_shutdown;
  int max_ues_on_target;
  int required_stable_low_load_reports;
  int required_neighbor_samples;
  int shutdown_confirmation_sec;
  int shutdown_timeout_sec;
  int kpm_max_age_sec;
  int cell_switch_cooldown_sec;
  int protected_cell_id;
  int decision_period_sec;
  int wait_for_e2_sec;
  bool dry_run;
  const char* csv_path;
} ee_policy_cfg_t;

static ee_policy_cfg_t g_policy = {
  .min_target_sinr_db = EE_DEFAULT_MIN_TARGET_SINR_DB,
  .min_handover_gain_db = EE_DEFAULT_MIN_HANDOVER_GAIN_DB,
  .max_ues_for_shutdown = EE_DEFAULT_MAX_UES_FOR_SHUTDOWN,
  .max_ues_on_target = EE_DEFAULT_MAX_UES_ON_TARGET,
  .required_stable_low_load_reports = EE_DEFAULT_REQUIRED_STABLE_LOW_LOAD_REPORTS,
  .required_neighbor_samples = EE_DEFAULT_REQUIRED_NEIGHBOR_SAMPLES,
  .shutdown_confirmation_sec = EE_DEFAULT_SHUTDOWN_CONFIRMATION_SEC,
  .shutdown_timeout_sec = EE_DEFAULT_SHUTDOWN_TIMEOUT_SEC,
  .kpm_max_age_sec = 5,
  .cell_switch_cooldown_sec = EE_DEFAULT_CELL_SWITCH_COOLDOWN_SEC,
  .protected_cell_id = EE_DEFAULT_PROTECTED_CELL_ID,
  .decision_period_sec = EE_DEFAULT_DECISION_PERIOD_SEC,
  .wait_for_e2_sec = EE_DEFAULT_WAIT_FOR_E2_SEC,
  .dry_run = false,
  .csv_path = "xapp_ee_decisions.csv",
};

static FILE* g_decision_log = NULL;
// Updated exclusively by real E2 KPM headers, patched to ns-3 microseconds.
static double simulation_time_s = 0.0;
static double policy_now(void) { return simulation_time_s; }
static void update_clock(uint64_t timestamp_us)
{
  const double report_time = timestamp_us / 1000000.0;
  if (report_time > simulation_time_s) {
    simulation_time_s = report_time;
    static double last_log = -1.0;
    if (report_time - last_log >= 1.0) {
      printf("[MyEnergy][KPM] simulation_time=%.6f s\n", report_time);
      last_log = report_time;
    }
  }
}

static int env_i(const char* name, int fallback)
{
  const char* v = getenv(name);
  if (v == NULL || *v == '\0')
    return fallback;

  char* end = NULL;
  errno = 0;
  long val = strtol(v, &end, 10);
  if (errno != 0 || end == v || *end != '\0' || val < INT_MIN || val > INT_MAX) {
    printf("[xApp][CFG] Invalid integer %s=%s, using %d\n", name, v, fallback);
    return fallback;
  }
  return (int)val;
}

static double env_d(const char* name, double fallback)
{
  const char* v = getenv(name);
  if (v == NULL || *v == '\0')
    return fallback;

  char* end = NULL;
  errno = 0;
  double val = strtod(v, &end);
  if (errno != 0 || end == v || *end != '\0' || !isfinite(val)) {
    printf("[xApp][CFG] Invalid double %s=%s, using %.3f\n", name, v, fallback);
    return fallback;
  }
  return val;
}

static bool env_b(const char* name, bool fallback)
{
  const char* v = getenv(name);
  if (v == NULL || *v == '\0')
    return fallback;

  return strcmp(v, "1") == 0 || strcasecmp(v, "true") == 0 || strcasecmp(v, "yes") == 0;
}

static void load_policy_from_env(void)
{
  g_policy.min_target_sinr_db = env_d("EE_MIN_TARGET_SINR_DB", g_policy.min_target_sinr_db);
  g_policy.min_handover_gain_db = env_d("EE_MIN_HANDOVER_GAIN_DB", g_policy.min_handover_gain_db);
  g_policy.max_ues_for_shutdown = env_i("EE_MAX_UES_FOR_SHUTDOWN", g_policy.max_ues_for_shutdown);
  g_policy.max_ues_on_target = env_i("EE_MAX_UES_ON_TARGET", g_policy.max_ues_on_target);
  g_policy.required_stable_low_load_reports = env_i("EE_STABLE_REPORTS", g_policy.required_stable_low_load_reports);
  g_policy.required_neighbor_samples = env_i("EE_REQUIRED_NEIGHBOR_SAMPLES", g_policy.required_neighbor_samples);
  g_policy.shutdown_confirmation_sec = env_i("EE_SHUTDOWN_CONFIRMATION_SEC", g_policy.shutdown_confirmation_sec);
  g_policy.shutdown_timeout_sec = env_i("EE_SHUTDOWN_TIMEOUT_SEC", g_policy.shutdown_timeout_sec);
  g_policy.kpm_max_age_sec = env_i("EE_KPM_MAX_AGE_SEC", g_policy.kpm_max_age_sec);
  g_policy.cell_switch_cooldown_sec = env_i("EE_CELL_SWITCH_COOLDOWN_SEC", g_policy.cell_switch_cooldown_sec);
  g_policy.protected_cell_id = env_i("EE_PROTECTED_CELL_ID", g_policy.protected_cell_id);
  g_policy.decision_period_sec = env_i("EE_DECISION_PERIOD_SEC", g_policy.decision_period_sec);
  g_policy.wait_for_e2_sec = env_i("EE_WAIT_FOR_E2_SEC", g_policy.wait_for_e2_sec);
  g_policy.dry_run = env_b("EE_DRY_RUN", g_policy.dry_run);

  if (g_policy.max_ues_for_shutdown < 0 || g_policy.max_ues_on_target < 1 ||
      g_policy.required_stable_low_load_reports < 1 || g_policy.required_neighbor_samples < 1 ||
      g_policy.shutdown_confirmation_sec < 0 || g_policy.shutdown_timeout_sec <= g_policy.shutdown_confirmation_sec ||
      g_policy.cell_switch_cooldown_sec < 0 || g_policy.decision_period_sec < 1 ||
      g_policy.wait_for_e2_sec < 1 || g_policy.kpm_max_age_sec < 1) {
    fprintf(stderr, "[xApp][CFG] Invalid policy range; refusing unsafe control policy\n");
    exit(EXIT_FAILURE);
  }

  const char* csv = getenv("EE_DECISION_LOG");
  if (csv != NULL && *csv != '\0')
    g_policy.csv_path = csv;

  printf("[xApp][CFG] minTargetSINR=%.2f dB, minHOGain=%.2f dB, maxUEsForShutdown=%d, maxUEsOnTarget=%d\n",
         g_policy.min_target_sinr_db, g_policy.min_handover_gain_db,
         g_policy.max_ues_for_shutdown, g_policy.max_ues_on_target);
  printf("[xApp][CFG] stableReports=%d, neighbourSamples=%d, confirm=%ds, timeout=%ds, cooldown=%ds, protectedCell=%d, dryRun=%d\n",
         g_policy.required_stable_low_load_reports, g_policy.required_neighbor_samples,
         g_policy.shutdown_confirmation_sec, g_policy.shutdown_timeout_sec,
         g_policy.cell_switch_cooldown_sec, g_policy.protected_cell_id, g_policy.dry_run);
}

static void open_decision_log(void)
{
  g_decision_log = fopen(g_policy.csv_path, "w");
  if (g_decision_log == NULL) {
    printf("[xApp][WARN] Cannot open decision log %s\n", g_policy.csv_path);
    return;
  }

  fprintf(g_decision_log, "time,event,cell_id,ue_id,ue_count,serving_sinr_db,target_cell,target_sinr_db,reason\n");
  fflush(g_decision_log);
}

static void decision_log(const char* event, uint16_t cell_id, int ue_id, size_t ue_count,
                         double serving_sinr, uint16_t target_cell, double target_sinr, const char* reason)
{
  if (g_decision_log == NULL)
    return;

  fprintf(g_decision_log, "%.6f,%s,%u,%d,%zu,%.3f,%u,%.3f,%s\n",
          policy_now(), event, cell_id, ue_id, ue_count, serving_sinr, target_cell, target_sinr,
          reason != NULL ? reason : "");
  fflush(g_decision_log);
}

struct SINRNeighboringValues 
{
  bool is_available;
  uint16_t neighCellID;
  double sinr;
  int counter;
  double last_update_time;
};

struct SINRServingValues 
{
  bool is_available;
  uint16_t ueID;
  double sinr;
  struct SINRNeighboringValues* neighCells;  // array, indexed by neighbour cell id
  size_t numOfNeighCells;
  bool handover_in_progress;
  uint16_t last_target_cell;
  double last_serving_update_time;
  double last_handover_time;
};

// Per Cell
struct SINR_Map 
{
  uint16_t cellID;
  struct SINRServingValues* connectedUEs;  // array, indexed by UE id
  size_t numOfConnectedUEs;
  bool is_running;
  bool is_off;
  bool pending_shutdown;
  double shutdown_start_time;
  double last_state_change_time;
  double last_kpm_update_time;
  int stable_low_load_reports;
  size_t confirmed_handover_count;
  double last_confirmation_time;
  uint64_t last_kpm_sequence;
  uint64_t last_evaluated_sequence;
};

struct registeredCells {
  bool is_registered;
  struct SINR_Map* sinrMap;
};

#define MAX_REGISTERED_CELLS 64
#define MAX_REGISTERED_UES 513
#define MAX_REGISTERED_NEIGHBOURS 64

struct registeredCells cells_sinr_map[MAX_REGISTERED_CELLS] = {{false, NULL}};
static uint64_t kpm_sequence = 0;



struct SINR_Map* add_SINR(const uint16_t cellID) 
{
  if (cellID == 0 || cellID >= MAX_REGISTERED_CELLS)
    return NULL;
  if (cells_sinr_map[cellID].sinrMap == NULL && !cells_sinr_map[cellID].is_registered) 
  {
    cells_sinr_map[cellID].sinrMap = (struct SINR_Map*)calloc(1, sizeof(struct SINR_Map));
    if (cells_sinr_map[cellID].sinrMap == NULL)
      return NULL;
    cells_sinr_map[cellID].is_registered = true;
    cells_sinr_map[cellID].sinrMap->cellID = cellID;
    cells_sinr_map[cellID].sinrMap->connectedUEs = NULL;
    cells_sinr_map[cellID].sinrMap->numOfConnectedUEs = 0;
    cells_sinr_map[cellID].sinrMap->is_running = true;
    cells_sinr_map[cellID].sinrMap->is_off = false;
    cells_sinr_map[cellID].sinrMap->pending_shutdown = false;
    cells_sinr_map[cellID].sinrMap->shutdown_start_time = 0;
    cells_sinr_map[cellID].sinrMap->last_state_change_time = 0;
    cells_sinr_map[cellID].sinrMap->last_kpm_update_time = policy_now();
    cells_sinr_map[cellID].sinrMap->stable_low_load_reports = 0;
  }
  if (cells_sinr_map[cellID].sinrMap != NULL) {
    cells_sinr_map[cellID].sinrMap->last_kpm_update_time = policy_now();
  }
  return cells_sinr_map[cellID].sinrMap;
}

// Serving msg
void add_UE(struct SINR_Map* cell, const uint16_t ueID, const double sinr) 
{
  if (cell == NULL || ueID == 0 || ueID >= MAX_REGISTERED_UES || !isfinite(sinr))
    return;

  if (cell->connectedUEs == NULL) {
    cell->connectedUEs = (struct SINRServingValues*)calloc(MAX_REGISTERED_UES, sizeof(struct SINRServingValues));
    if (cell->connectedUEs == NULL)
      return;
  }

  struct SINRServingValues* ue = &cell->connectedUEs[ueID];
  if (!ue->is_available) {
    cell->numOfConnectedUEs += 1;
    ue->ueID = ueID;
    ue->neighCells = NULL;
    ue->numOfNeighCells = 0;
    ue->handover_in_progress = false;
    ue->last_target_cell = 0;
    ue->last_handover_time = 0;
  }

  ue->sinr = sinr;
  ue->last_serving_update_time = policy_now();
  ue->is_available = true;
  cell->last_kpm_update_time = policy_now();
  cell->last_kpm_sequence = kpm_sequence;

  // A serving report from the commanded destination is positive evidence of
  // relocation. Silence or a successful RC submission is not confirmation.
  for (int i = 1; i < MAX_REGISTERED_CELLS; ++i) {
    struct SINR_Map* previous = cells_sinr_map[i].sinrMap;
    if (previous == NULL || previous == cell || previous->connectedUEs == NULL)
      continue;
    struct SINRServingValues* old = &previous->connectedUEs[ueID];
    if (old->is_available && old->handover_in_progress && old->last_target_cell == cell->cellID) {
      decision_log("handover_confirmed", previous->cellID, ueID, previous->numOfConnectedUEs,
                   old->sinr, cell->cellID, sinr, "serving_report_from_target");
      previous->confirmed_handover_count++;
      previous->last_confirmation_time = policy_now();
      old->is_available = false;
      old->handover_in_progress = false;
      free(old->neighCells);
      old->neighCells = NULL;
      old->numOfNeighCells = 0;
      if (previous->numOfConnectedUEs > 0)
        previous->numOfConnectedUEs--;
    }
  }
}


struct SINRServingValues* get_UE(const uint16_t cellID, const uint16_t ueID) 
{
  if (cellID < MAX_REGISTERED_CELLS && ueID < MAX_REGISTERED_UES && cells_sinr_map[cellID].is_registered) {
    if (cells_sinr_map[cellID].sinrMap == NULL || cells_sinr_map[cellID].sinrMap->connectedUEs == NULL)
      return NULL;
    if (!cells_sinr_map[cellID].sinrMap->connectedUEs[ueID].is_available)
      return NULL;

    return &(cells_sinr_map[cellID].sinrMap->connectedUEs[ueID]);
    // printf("x->ueID=%d, ueID=%d\n", x->ueID , ueID);
    // return x;
    // for (size_t i = 0; i < cells_sinr_map[cellID].sinrMap->numOfConnectedUEs; i++)
    // {
    //   if(cells_sinr_map[cellID].sinrMap->connectedUEs[i].ueID == ueID) {
    //     return &cells_sinr_map[cellID].sinrMap->connectedUEs[i];
    //   }
    // }
  }
  return NULL;
}



// m_rrc
// m_lastMmWaveCell[[a-z]+\] =
// m_lastMmWaveCell[m_[a-z]+\] =
// m_lastMmWaveCell[m
// DoRecvLteMmWaveHandoverCompleted
// UeManager::RecvRrcConnectionReconfigurationCompleted(MC_CONNECTION_RECONFIGURATION OR CONNECTION_RECONFIGURATION)

// LteEnbRrc::DoRecvRrcConnectionReconfigurationCompleted
// LteEnbRrc::DoRecvLteMmWaveHandoverCompleted --> IMP (m_lastMmWaveCell[imsi] = params.targetCellId;)


void add_neighCell(struct SINRServingValues* UE, const uint16_t neighCellID, const double sinr) 
{
  if (UE == NULL || !isfinite(sinr))
    return;
  if (neighCellID == 0 || neighCellID >= MAX_REGISTERED_NEIGHBOURS) {
    printf("Ignoring neighbour cell %d: outside MAX_REGISTERED_NEIGHBOURS=%d\n", neighCellID, MAX_REGISTERED_NEIGHBOURS);
    return;
  }

  // Initialize neighbor cells array if needed
  if (UE->neighCells == NULL) {
    UE->neighCells = (struct SINRNeighboringValues*)calloc(MAX_REGISTERED_NEIGHBOURS,
                                                           sizeof(struct SINRNeighboringValues));
    if (UE->neighCells == NULL)
      return;
  }

  // Get reference to this neighbor cell
  struct SINRNeighboringValues* neighCell = &UE->neighCells[neighCellID];

  const double now = policy_now();
  if (!neighCell->is_available || now - neighCell->last_update_time > g_policy.kpm_max_age_sec) {
    if (!neighCell->is_available)
      UE->numOfNeighCells++;
    neighCell->counter = 0;
  }
  // Use the latest SINR for the safety threshold; an old average can hide a
  // deteriorating link. The sample counter proves repeated recent observations.
  neighCell->neighCellID = neighCellID;
  neighCell->sinr = sinr;
  if (neighCell->counter < g_policy.required_neighbor_samples)
    neighCell->counter++;
  neighCell->last_update_time = now;
  neighCell->is_available = true;
}

static size_t pending_incoming_ues(uint16_t target_cell)
{
  size_t count = 0;
  for (int i = 1; i < MAX_REGISTERED_CELLS; ++i) {
    struct SINR_Map const* cell = cells_sinr_map[i].sinrMap;
    if (cell == NULL || cell->connectedUEs == NULL)
      continue;
    for (int j = 1; j < MAX_REGISTERED_UES; ++j) {
      struct SINRServingValues const* ue = &cell->connectedUEs[j];
      if (ue->is_available && ue->handover_in_progress && ue->last_target_cell == target_cell)
        count++;
    }
  }
  return count;
}

uint16_t getTargetCellID(callback_data_t data) 
{
  assert(data.neighCells != NULL);

  double best_score = -1000000.0;
  uint16_t targetCell = 0;
  double selected_sinr = -1000.0;

  printf("\n=== Target Cell Selection for UE %d ===\n", data.ueID);
  printf("Current Cell: %d, serving SINR: %.2f dB\n", data.frmCurntCell, data.servingSinr);

  for (int i = 0; i < MAX_REGISTERED_NEIGHBOURS; i++)
  {
    struct SINRNeighboringValues* neigh = &data.neighCells[i];
    if (!neigh->is_available || !isfinite(neigh->sinr) ||
        policy_now() - neigh->last_update_time > g_policy.kpm_max_age_sec)
      continue;
    // Unknown target load is not zero load. The demo RC cell encoding only
    // supports single-digit IDs.
    if (i == 0 || i > 9 || cells_sinr_map[i].sinrMap == NULL ||
        policy_now() - cells_sinr_map[i].sinrMap->last_kpm_update_time > g_policy.kpm_max_age_sec)
      continue;

    if (neigh->counter < g_policy.required_neighbor_samples) {
      printf("Skipping Cell %d: only %d/%d SINR samples\n", i, neigh->counter, g_policy.required_neighbor_samples);
      continue;
    }

    if ((uint16_t)i == data.frmCurntCell) {
      printf("Skipping Cell %d: same as serving cell\n", i);
      continue;
    }

    if (cells_sinr_map[i].sinrMap != NULL) {
      struct SINR_Map* target = cells_sinr_map[i].sinrMap;
      if (target->pending_shutdown || target->is_off || !target->is_running) {
        printf("Skipping Cell %d: not a safe target (pending/off/not running)\n", i);
        continue;
      }
      if ((int)(target->numOfConnectedUEs + pending_incoming_ues((uint16_t)i)) >= g_policy.max_ues_on_target) {
        printf("Skipping Cell %d: target load guard UE=%zu/%d\n", i, target->numOfConnectedUEs, g_policy.max_ues_on_target);
        continue;
      }
    }

    double gain = neigh->sinr - data.servingSinr;
    if (neigh->sinr < g_policy.min_target_sinr_db) {
      printf("Skipping Cell %d: SINR %.2f dB < %.2f dB\n", i, neigh->sinr, g_policy.min_target_sinr_db);
      continue;
    }
    if (gain < g_policy.min_handover_gain_db) {
      printf("Skipping Cell %d: HO gain %.2f dB < %.2f dB\n", i, gain, g_policy.min_handover_gain_db);
      continue;
    }

    size_t target_ues = 0;
    if (cells_sinr_map[i].sinrMap != NULL)
      target_ues = cells_sinr_map[i].sinrMap->numOfConnectedUEs;

    double score = neigh->sinr - 0.25 * (double)target_ues;
    printf("Evaluating Cell %d: SINR %.2f dB, gain %.2f dB, targetUE=%zu, score %.2f\n",
           i, neigh->sinr, gain, target_ues, score);

    if (score > best_score) {
      best_score = score;
      selected_sinr = neigh->sinr;
      targetCell = (uint16_t)i;
    }
  }

  if (targetCell != 0) {
    printf("Selected Target Cell: %d (target SINR: %.2f dB, score: %.2f)\n", targetCell, selected_sinr, best_score);
  } else {
    printf("No suitable target cell found (target SINR >= %.2f dB required)\n", g_policy.min_target_sinr_db);
  }

  return targetCell;
}

// TODO

// T Search(fp);
// GetIMSI, getSINR, findX, getTargetCell(sinrMap, targetCell)
// seach(findXX);

// static
// void log_kpm_measurements(kpm_ind_msg_format_1_t const* msg_frm_1)
// {
//   if (msg_frm_1 == NULL || msg_frm_1->meas_info_lst_len == 0)
//   // UE Measurements per granularity period
//   for (size_t j = 0; j < msg_frm_1->meas_data_lst_len; j++) {
//     meas_data_lst_t const data_item = msg_frm_1->meas_data_lst[j];
//     for (size_t z = 0; z < data_item.meas_record_len; z++) {
//       meas_type_t const meas_type = msg_frm_1->meas_info_lst[z].meas_type;
//       meas_record_lst_t const record_item = data_item.meas_record_lst[z];
//       match_meas_type[meas_type.type](meas_type, record_item);
//       if (data_item.incomplete_flag && *data_item.incomplete_flag == TRUE_ENUM_VALUE)
//         printf("Measurement Record not reliable");
//     }
//   }
// }
struct InfoObj 
{
  uint16_t cellID;
  uint16_t ueID;
};

// struct InfoObj parseStr()
struct InfoObj parseServingMsg(const char* msg) 
{
  struct InfoObj info;

  int ret = sscanf(msg, "L3servingSINR3gpp_cell_%hu_UEID_%hu", &info.cellID, &info.ueID);

  if (ret == 2)
    return info;

  info.cellID = -1;
  info.ueID = -1;
  return info;
}

struct InfoObj parseNeighMsg(const char* msg) 
{
  struct InfoObj info;

  int ret = sscanf(msg, "L3neighSINRListOf_UEID_%hu_of_Cell_%hu", &info.ueID, &info.cellID);

  if (ret == 2)
    return info;

  info.ueID = -1;
  info.cellID = -1;
  return info;
}

bool isMeasNameContains(const char* meas_name, const char* name) 
{
  return strncmp(meas_name, name, strlen(name)) == 0;
}

static void log_kpm_measurements(kpm_ind_msg_format_1_t const* msg_frm_1) 
{
  if (msg_frm_1 == NULL || msg_frm_1->meas_info_lst_len == 0)
    return;
  static unsigned debug_reports = 0;
  if (debug_reports++ < 3)
    printf("[MyEnergy][KPM] names=%zu rows=%zu\n", msg_frm_1->meas_info_lst_len, msg_frm_1->meas_data_lst_len);

  // assert(msg_frm_1->meas_info_lst_len == msg_frm_1->meas_data_lst_len && "meas_info_lst_len not equal
  // meas_data_lst_len");
  if (msg_frm_1->meas_info_lst_len != msg_frm_1->meas_data_lst_len) 
  {
    printf("Error: meas_info_lst_len= (%ld) not equal meas_data_lst_len= (%ld)\n", msg_frm_1->meas_info_lst_len,
           msg_frm_1->meas_data_lst_len);
    return;
  }

  // UE Measurements per granularity period
  for (size_t i = 0; i < msg_frm_1->meas_info_lst_len; i++) 
  {
    meas_type_t const meas_type = msg_frm_1->meas_info_lst[i].meas_type;
    meas_data_lst_t const data_item = msg_frm_1->meas_data_lst[i];
    // ASN.1 OCTET STRING is length-delimited, not guaranteed NUL-terminated.
    char measurement_name[256] = {0};
    if (meas_type.type != NAME_MEAS_TYPE || meas_type.name.buf == NULL ||
        meas_type.name.len >= sizeof(measurement_name))
      continue;
    memcpy(measurement_name, meas_type.name.buf, meas_type.name.len);
    if (debug_reports <= 3)
      printf("[MyEnergy][KPM] name=%s records=%zu\n", measurement_name, data_item.meas_record_len);

    for (size_t j = 0; j < data_item.meas_record_len;) {
      meas_record_lst_t const record_item = data_item.meas_record_lst[j];

      if (meas_type.type == NAME_MEAS_TYPE) 
      {
        if (isMeasNameContains(measurement_name, "L3servingSINR3gpp_cell_")) 
        {
          struct InfoObj info = parseServingMsg(measurement_name);
          if (record_item.value != REAL_MEAS_VALUE) {
            j++;
            continue;
          }
          double sinr = record_item.real_val;

          struct SINR_Map* cell = add_SINR(info.cellID);
          add_UE(cell, info.ueID, sinr);

          printf("Serving Cell %d - UE %d: %.2f dB\n", info.cellID, info.ueID, sinr);

        } else if (isMeasNameContains(measurement_name, "L3neighSINRListOf_UEID_")) 
        {
          struct InfoObj info = parseNeighMsg(measurement_name);

          if (j + 1 >= data_item.meas_record_len || record_item.value != REAL_MEAS_VALUE ||
              data_item.meas_record_lst[j + 1].value != INTEGER_MEAS_VALUE)
            break;
          meas_record_lst_t const sinr = record_item;
          meas_record_lst_t const NeighbourID = data_item.meas_record_lst[j + 1];

          struct SINRServingValues* UE = get_UE(info.cellID, info.ueID);
          if (UE == NULL) {
            printf("Ignoring neighbour SINR for UE %d in Cell %d: serving record not available yet\n", info.ueID, info.cellID);
            j += 2;
            continue;
          }

          add_neighCell(UE, NeighbourID.int_val, sinr.real_val);
          j += 2;
          continue;
        }
      }
      j++;
    }
  }
}

static void sm_cb_kpm(sm_ag_if_rd_t const* rd) 
{
  assert(rd != NULL);
  assert(rd->type == INDICATION_MSG_AGENT_IF_ANS_V0);
  assert(rd->ind.type == KPM_STATS_V3_0);

  // Reading Indication Message Format 3
  kpm_ind_data_t const* ind = &rd->ind.kpm.ind;
  static unsigned indication_count = 0;
  if (++indication_count <= 3)
    printf("[MyEnergy][KPM] indication_type=%d\n", ind->msg.type);
  if (ind->msg.type == FORMAT_1_INDICATION_MESSAGE) {
    lock_guard(&mtx);
    update_clock(ind->hdr.kpm_ric_ind_hdr_format_1.collectStartTime);
    ++kpm_sequence;
    log_kpm_measurements(&ind->msg.frm_1);
    return;
  }
  if (ind->msg.type != FORMAT_3_INDICATION_MESSAGE)
    return;
  kpm_ind_msg_format_3_t const* msg_frm_3 = &ind->msg.frm_3;

  // uint64_t const now = time_now_us();
  {
    lock_guard(&mtx);
    update_clock(ind->hdr.kpm_ric_ind_hdr_format_1.collectStartTime);
    ++kpm_sequence;
    // printf("\n time now = %ld \n",now);
    // printf("\n time from simulator = %ld \n", hdr_frm_1->collectStartTime); //ntohll(hdr_frm_1->collectStartTime)
    // printf("\n%7d KPM ind_msg latency = %ld [μs]\n", counter, now -  hdr_frm_1->collectStartTime); // xApp <-> E2 Node

    // Reported list of measurements per UE
    for (size_t i = 0; i < msg_frm_3->ue_meas_report_lst_len; i++) 
    {
      // log measurements
      log_kpm_measurements(&msg_frm_3->meas_report_per_ue[i].ind_msg_format_1);
    }
  }
}

static test_info_lst_t filter_predicate(test_cond_type_e type, test_cond_e cond, int value) 
{
  test_info_lst_t dst = {0};

  dst.test_cond_type = type;
  dst.IsStat = TRUE_TEST_COND_TYPE;

  // Allocate memory for test_cond and set its value
  dst.test_cond = calloc(1, sizeof(test_cond_e));
  assert(dst.test_cond != NULL && "Memory allocation failed for test_cond");
  *dst.test_cond = cond;

  // Allocate memory for test_cond_value
  dst.test_cond_value = calloc(1, sizeof(test_cond_value_t));
  assert(dst.test_cond_value != NULL && "Memory allocation failed for test_cond_value");
  dst.test_cond_value->type = INTEGER_TEST_COND_VALUE;

  // Allocate memory for int_value and set its value
  int64_t* int_value = calloc(1, sizeof(int64_t));
  assert(int_value != NULL && "Memory allocation failed for int_value");
  *int_value = value;
  dst.test_cond_value->int_value = int_value;
  return dst;
}

static label_info_lst_t fill_kpm_label(void) 
{
  label_info_lst_t label_item = {0};

  label_item.noLabel = ecalloc(1, sizeof(enum_value_e));
  *label_item.noLabel = TRUE_ENUM_VALUE;

  return label_item;
}

static kpm_act_def_format_1_t fill_act_def_frm_1(ric_report_style_item_t const* report_item) 
{
  assert(report_item != NULL);

  kpm_act_def_format_1_t ad_frm_1 = {0};

  size_t const sz = report_item->meas_info_for_action_lst_len;

  // [1, 65535]
  ad_frm_1.meas_info_lst_len = sz;
  ad_frm_1.meas_info_lst = calloc(sz, sizeof(meas_info_format_1_lst_t));
  assert(ad_frm_1.meas_info_lst != NULL && "Memory exhausted");

  for (size_t i = 0; i < sz; i++) 
  {
    meas_info_format_1_lst_t* meas_item = &ad_frm_1.meas_info_lst[i];
    // 8.3.9
    // Measurement Name
    meas_item->meas_type.type = NAME_MEAS_TYPE;
    meas_item->meas_type.name = copy_byte_array(report_item->meas_info_for_action_lst[i].name);

    // [1, 2147483647]
    // 8.3.11
    meas_item->label_info_lst_len = 1;
    meas_item->label_info_lst = ecalloc(1, sizeof(label_info_lst_t));
    meas_item->label_info_lst[0] = fill_kpm_label();
  }

  // 8.3.8 [0, 4294967295]
  ad_frm_1.gran_period_ms = period_ms;

  // 8.3.20 - OPTIONAL
  ad_frm_1.cell_global_id = NULL;

#if defined KPM_V2_03 || defined KPM_V3_00
  // [0, 65535]
  ad_frm_1.meas_bin_range_info_lst_len = 0;
  ad_frm_1.meas_bin_info_lst = NULL;
#endif

  return ad_frm_1;
}

static kpm_act_def_t fill_report_style_4(ric_report_style_item_t const* report_item) 
{
  assert(report_item != NULL);
  assert(report_item->act_def_format_type == FORMAT_4_ACTION_DEFINITION);

  kpm_act_def_t act_def = {.type = FORMAT_4_ACTION_DEFINITION};

  // Fill matching condition
  // [1, 32768]
  act_def.frm_4.matching_cond_lst_len = 1;
  act_def.frm_4.matching_cond_lst =
      calloc(act_def.frm_4.matching_cond_lst_len, sizeof(matching_condition_format_4_lst_t));
  assert(act_def.frm_4.matching_cond_lst != NULL && "Memory exhausted");
  // Filter connected UEs by S-NSSAI criteria
  test_cond_type_e const type = IsStat_TEST_COND_TYPE;  // CQI_TEST_COND_TYPE
  test_cond_e const condition = LESSTHAN_TEST_COND;     // GREATERTHAN_TEST_COND
  int const value = 40;
  act_def.frm_4.matching_cond_lst[0].test_info_lst = filter_predicate(type, condition, value);

  // Fill Action Definition Format 1
  // 8.2.1.2.1
  act_def.frm_4.action_def_format_1 = fill_act_def_frm_1(report_item);

  return act_def;
}

typedef kpm_act_def_t (*fill_kpm_act_def)(ric_report_style_item_t const* report_item);

static fill_kpm_act_def get_kpm_act_def[END_RIC_SERVICE_REPORT] = 
{
    NULL,
    NULL,
    NULL,
    fill_report_style_4,
    NULL,
};

static kpm_sub_data_t gen_kpm_subs(kpm_ran_function_def_t const* ran_func) 
{
  assert(ran_func != NULL);
  assert(ran_func->ric_event_trigger_style_list != NULL);

  kpm_sub_data_t kpm_sub = {0};

  // Generate Event Trigger
  assert(ran_func->ric_event_trigger_style_list[0].format_type == FORMAT_1_RIC_EVENT_TRIGGER);
  kpm_sub.ev_trg_def.type = FORMAT_1_RIC_EVENT_TRIGGER;
  kpm_sub.ev_trg_def.kpm_ric_event_trigger_format_1.report_period_ms = period_ms;

  // Generate Action Definition
  kpm_sub.sz_ad = 1;
  kpm_sub.ad = calloc(kpm_sub.sz_ad, sizeof(kpm_act_def_t));
  assert(kpm_sub.ad != NULL && "Memory exhausted");

  // Multiple Action Definitions in one SUBSCRIPTION message is not supported in this project
  // Multiple REPORT Styles = Multiple Action Definition = Multiple SUBSCRIPTION messages
  ric_report_style_item_t* const report_item = &ran_func->ric_report_style_list[0];
  ric_service_report_e const report_style_type = report_item->report_style_type;
  *kpm_sub.ad = get_kpm_act_def[report_style_type](report_item);

  return kpm_sub;
}

static size_t find_sm_idx(sm_ran_function_t* rf, size_t sz, bool (*f)(sm_ran_function_t const*, int const),
                          int const id) 
{
  for (size_t i = 0; i < sz; i++) 
  {
    if (f(&rf[i], id))
      return i;
  }

  assert(0 != 0 && "SM ID could not be found in the RAN Function List");
}

//*************************************************************** *//
static e2sm_rc_ctrl_hdr_frmt_1_t gen_rc_ctrl_hdr_frmt_1(ue_id_e2sm_t ue_id, uint32_t ric_style_type,
                                                         uint16_t ctrl_act_id) {
  e2sm_rc_ctrl_hdr_frmt_1_t dst = {0};

  // 6.2.2.6
  dst.ue_id = cp_ue_id_e2sm(&ue_id);

  dst.ric_style_type = ric_style_type;
  dst.ctrl_act_id = ctrl_act_id;

  return dst;
}

static e2sm_rc_ctrl_hdr_t gen_rc_ctrl_hdr(e2sm_rc_ctrl_hdr_e hdr_frmt, ue_id_e2sm_t ue_id,
                                           uint32_t ric_style_type, uint16_t ctrl_act_id) 
{
  e2sm_rc_ctrl_hdr_t dst = {0};

  if (hdr_frmt == FORMAT_1_E2SM_RC_CTRL_HDR) {
    dst.format = FORMAT_1_E2SM_RC_CTRL_HDR;
    dst.frmt_1 = gen_rc_ctrl_hdr_frmt_1(ue_id, ric_style_type, ctrl_act_id);
  } else 
  {
    assert(0 != 0 && "not implemented the fill func for this ctrl hdr frmt");
  }

  return dst;
}

static void set_EUTRA_CGI(seq_ran_param_t* EUTRA_CGI, const char targetcell) 
{
  // Input validation
  assert(EUTRA_CGI != NULL);
  assert(targetcell >= '0' && targetcell <= '9');

  // Validate flag_false is allocated
  if (EUTRA_CGI->ran_param_val.flag_false == NULL) 
  {
    EUTRA_CGI->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(EUTRA_CGI->ran_param_val.flag_false != NULL && "Memory exhausted");
  }

  EUTRA_CGI->ran_param_val.flag_false->type = BIT_STRING_RAN_PARAMETER_VALUE;

  // Convert target cell to byte array
  byte_array_t target_ba = {0};
  target_ba.len = 1;
  target_ba.buf = malloc(sizeof(uint8_t));
  assert(target_ba.buf != NULL && "Memory exhausted");
  target_ba.buf[0] = targetcell;

  EUTRA_CGI->ran_param_val.flag_false->octet_str_ran.len = target_ba.len;
  EUTRA_CGI->ran_param_val.flag_false->octet_str_ran.buf = target_ba.buf;
}

static void gen_Target_Primary_Cell_ID(seq_ran_param_t* Target_Primary_Cell_ID, char targetcell) 
{
  // Target Primary Cell ID, STRUCTURE (len 1)

  Target_Primary_Cell_ID->ran_param_id = TARGET_PRIMARY_CELL_ID_8_4_4_1;
  Target_Primary_Cell_ID->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  Target_Primary_Cell_ID->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
  assert(Target_Primary_Cell_ID->ran_param_val.strct != NULL && "Memory exhausted");
  Target_Primary_Cell_ID->ran_param_val.strct->sz_ran_param_struct = 1;
  Target_Primary_Cell_ID->ran_param_val.strct->ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
  assert(Target_Primary_Cell_ID->ran_param_val.strct->ran_param_struct != NULL && "Memory exhausted");

  // > CHOICE Target Cell, STRUCTURE (len 2)
  seq_ran_param_t* CHOICE_Target_Cell = &Target_Primary_Cell_ID->ran_param_val.strct->ran_param_struct[0];
  CHOICE_Target_Cell->ran_param_id = CHOICE_TARGET_CELL_8_4_4_1;
  CHOICE_Target_Cell->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  CHOICE_Target_Cell->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
  assert(CHOICE_Target_Cell->ran_param_val.strct != NULL && "Memory exhausted");
  CHOICE_Target_Cell->ran_param_val.strct->sz_ran_param_struct = 2;
  CHOICE_Target_Cell->ran_param_val.strct->ran_param_struct = calloc(2, sizeof(seq_ran_param_t));
  assert(CHOICE_Target_Cell->ran_param_val.strct->ran_param_struct != NULL && "Memory exhausted");

  // >>  NR Cell, STRUCTURE (len 1))
  seq_ran_param_t* NR_Cell = &CHOICE_Target_Cell->ran_param_val.strct->ran_param_struct[0];
  NR_Cell->ran_param_id = NR_CELL_8_4_4_1;
  NR_Cell->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  NR_Cell->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
  assert(NR_Cell->ran_param_val.strct != NULL && "Memory exhausted");
  NR_Cell->ran_param_val.strct->sz_ran_param_struct = 1;
  NR_Cell->ran_param_val.strct->ran_param_struct = calloc(1, sizeof(seq_ran_param_t));

  // >>> NR CGI, ELEMENT   NR CGI is usually written in the format: PLMN ID + NR Cell Identity.
  seq_ran_param_t* NR_CGI = &NR_Cell->ran_param_val.strct->ran_param_struct[0];
  NR_CGI->ran_param_id = NR_CGI_8_4_4_1;
  NR_CGI->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
  NR_CGI->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(NR_CGI->ran_param_val.flag_false != NULL && "Memory exhausted");
  NR_CGI->ran_param_val.flag_false->type = BIT_STRING_RAN_PARAMETER_VALUE;
  // NR_CGI->ran_param_val.flag_false->int_ran=  TARGET_CELL;
  char nr_cgi_str[2] = {targetcell, '\0'};

  byte_array_t nr_cgi = cp_str_to_ba(nr_cgi_str);
  NR_CGI->ran_param_val.flag_false->octet_str_ran.len = nr_cgi.len;
  NR_CGI->ran_param_val.flag_false->octet_str_ran.buf = nr_cgi.buf;

  // >>E-UTRA Cell, STRUCTURE (len 1)
  seq_ran_param_t* EUTRA_Cell = &CHOICE_Target_Cell->ran_param_val.strct->ran_param_struct[1];
  EUTRA_Cell->ran_param_id = EUTRA_CELL_8_4_4_1;
  EUTRA_Cell->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  EUTRA_Cell->ran_param_val.strct = calloc(1, sizeof(ran_param_struct_t));
  assert(EUTRA_Cell->ran_param_val.strct != NULL && "Memory exhausted");
  EUTRA_Cell->ran_param_val.strct->sz_ran_param_struct = 1;
  EUTRA_Cell->ran_param_val.strct->ran_param_struct = calloc(1, sizeof(seq_ran_param_t));

  // >>>E-UTRA CGI, ELEMENT  The E-UTRA CGI is typically written in the format: PLMN ID + E-UTRAN Cell Identity.
  seq_ran_param_t* EUTRA_CGI = &EUTRA_Cell->ran_param_val.strct->ran_param_struct[0];
  EUTRA_CGI->ran_param_id = EUTRA_CGI_8_4_4_1;
  EUTRA_CGI->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
  EUTRA_CGI->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(EUTRA_CGI->ran_param_val.flag_false != NULL && "Memory exhausted");
  EUTRA_CGI->ran_param_val.flag_false->type = BIT_STRING_RAN_PARAMETER_VALUE;

  set_EUTRA_CGI(EUTRA_CGI, targetcell);

  // char eUTRA_cgi_str [2] ;
  // eUTRA_cgi_str[0] = targetcell;
  // eUTRA_cgi_str [1] = '\0';

  // byte_array_t eUTRA_cgi = cp_str_to_ba(eUTRA_cgi_str);
  // EUTRA_CGI->ran_param_val.flag_false->octet_str_ran.len = eUTRA_cgi.len;
  // EUTRA_CGI->ran_param_val.flag_false->octet_str_ran.buf = eUTRA_cgi.buf;
  return;
}

static void gen_List_of_PDU_sessions_for_handover(seq_ran_param_t* List_PDU_sessions_ho) 
{
  int num_PDU_session = 1;

  // List of PDU sessions for handover, LIST (len 1)
  List_PDU_sessions_ho->ran_param_id = LIST_OF_PDU_SESSIONS_FOR_HANDOVER_8_4_4_1;
  List_PDU_sessions_ho->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_PDU_sessions_ho->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_PDU_sessions_ho->ran_param_val.lst != NULL && "Memory exhausted");
  List_PDU_sessions_ho->ran_param_val.lst->sz_lst_ran_param = num_PDU_session;
  List_PDU_sessions_ho->ran_param_val.lst->lst_ran_param = calloc(num_PDU_session, sizeof(lst_ran_param_t));
  assert(List_PDU_sessions_ho->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  // > PDU session Item for handover, STRUCTURE (len 2)
  lst_ran_param_t* PDU_session_item = &List_PDU_sessions_ho->ran_param_val.lst->lst_ran_param[0];
  PDU_session_item->ran_param_struct.sz_ran_param_struct = 2;
  PDU_session_item->ran_param_struct.ran_param_struct = calloc(2, sizeof(seq_ran_param_t));
  assert(PDU_session_item->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  // >> PDU Session ID, ELEMENT
  seq_ran_param_t* PDU_Session_ID = &PDU_session_item->ran_param_struct.ran_param_struct[0];
  PDU_Session_ID->ran_param_id = PDU_SESSION_ID_8_4_4_1;
  PDU_Session_ID->ran_param_val.type = ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
  PDU_Session_ID->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(PDU_Session_ID->ran_param_val.flag_false != NULL && "Memory exhausted");
  PDU_Session_ID->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;

  // Use enum for PDU Session ID
  char pduid_str[2];
  snprintf(pduid_str, sizeof(pduid_str), "%d", PDU_SESSION_ID_5);
  byte_array_t pduid = cp_str_to_ba(pduid_str);
  PDU_Session_ID->ran_param_val.flag_false->octet_str_ran.len = pduid.len;
  PDU_Session_ID->ran_param_val.flag_false->octet_str_ran.buf = pduid.buf;

  // >> List of QoS flows in the PDU session, LIST (len 1)
  seq_ran_param_t* List_of_QoS_flows = &PDU_session_item->ran_param_struct.ran_param_struct[1];
  List_of_QoS_flows->ran_param_id = LIST_OF_QOS_FLOWS_IN_THE_PDU_SESSION_8_4_4_1;
  List_of_QoS_flows->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_of_QoS_flows->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_of_QoS_flows->ran_param_val.lst != NULL && "Memory exhausted");
  List_of_QoS_flows->ran_param_val.lst->sz_lst_ran_param = 1;
  List_of_QoS_flows->ran_param_val.lst->lst_ran_param = calloc(1, sizeof(lst_ran_param_t));
  assert(List_of_QoS_flows->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  // >>> QoS flow Item, STRUCTURE (len 1)
  lst_ran_param_t* QoS_flow_Item = &List_of_QoS_flows->ran_param_val.lst->lst_ran_param[0];
  QoS_flow_Item->ran_param_struct.sz_ran_param_struct = 1;
  QoS_flow_Item->ran_param_struct.ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
  assert(QoS_flow_Item->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  // >>>> QoS Flow Identifier, ELEMENT
  seq_ran_param_t* QoS_Flow_Id = &QoS_flow_Item->ran_param_struct.ran_param_struct[0];
  QoS_Flow_Id->ran_param_id = QOS_FLOW_IDENTIFIER_8_4_4_1;
  QoS_Flow_Id->ran_param_val.type = ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
  QoS_Flow_Id->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(QoS_Flow_Id->ran_param_val.flag_false != NULL && "Memory exhausted");
  QoS_Flow_Id->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;

  // Use enum for QoS Flow ID
  char qosid_str[3];
  snprintf(qosid_str, sizeof(qosid_str), "%d", QOS_FLOW_ID_1);
  byte_array_t qosid = cp_str_to_ba(qosid_str);
  QoS_Flow_Id->ran_param_val.flag_false->octet_str_ran.len = qosid.len;
  QoS_Flow_Id->ran_param_val.flag_false->octet_str_ran.buf = qosid.buf;

  return;
}

static void gen_List_of_DRBs_for_handover(seq_ran_param_t* List_DRBs_ho) 
{
  int num_DRBs = 1;
  // List of DRBs for handover, LIST (len 1)
  List_DRBs_ho->ran_param_id = LIST_OF_DRBS_FOR_HANDOVER_8_4_4_1;
  List_DRBs_ho->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_DRBs_ho->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_DRBs_ho->ran_param_val.lst != NULL && "Memory exhausted");
  List_DRBs_ho->ran_param_val.lst->sz_lst_ran_param = num_DRBs;
  List_DRBs_ho->ran_param_val.lst->lst_ran_param = calloc(num_DRBs, sizeof(lst_ran_param_t));
  assert(List_DRBs_ho->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  // > DRB item for handover, STRUCTURE (len 2)
  lst_ran_param_t* DRB_item_ho = &List_DRBs_ho->ran_param_val.lst->lst_ran_param[0];

  DRB_item_ho->ran_param_struct.sz_ran_param_struct = 2;
  DRB_item_ho->ran_param_struct.ran_param_struct = calloc(2, sizeof(seq_ran_param_t));
  assert(DRB_item_ho->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  // >> DRB ID, ELEMENT
  seq_ran_param_t* DRB_ID = &DRB_item_ho->ran_param_struct.ran_param_struct[0];
  DRB_ID->ran_param_id = DRB_ID_8_4_4_1;
  DRB_ID->ran_param_val.type = ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
  DRB_ID->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(DRB_ID->ran_param_val.flag_false != NULL && "Memory exhausted");
  DRB_ID->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  char DRB_ID_str[] = "3";
  byte_array_t drpID = cp_str_to_ba(DRB_ID_str);
  DRB_ID->ran_param_val.flag_false->octet_str_ran.len = drpID.len;
  DRB_ID->ran_param_val.flag_false->octet_str_ran.buf = drpID.buf;

  // >> List of QoS flows in the DRB, LIST (len 1)
  seq_ran_param_t* List_of_QoS_flows = &DRB_item_ho->ran_param_struct.ran_param_struct[1];  //////.....//////
  List_of_QoS_flows->ran_param_id = LIST_OF_QOS_FLOWS_IN_THE_DRB_8_4_4_1;
  List_of_QoS_flows->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_of_QoS_flows->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_of_QoS_flows->ran_param_val.lst != NULL && "Memory exhausted");
  List_of_QoS_flows->ran_param_val.lst->sz_lst_ran_param = 1;
  List_of_QoS_flows->ran_param_val.lst->lst_ran_param = calloc(1, sizeof(lst_ran_param_t));
  assert(List_of_QoS_flows->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  // >>>QoS flow Item, STRUCTURE (len 1)
  lst_ran_param_t* QoS_flow_Item = &List_of_QoS_flows->ran_param_val.lst->lst_ran_param[0];
  QoS_flow_Item->ran_param_struct.sz_ran_param_struct = 1;
  QoS_flow_Item->ran_param_struct.ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
  assert(QoS_flow_Item->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  // >>>>QoS Flow Identifier, ELEMENT
  seq_ran_param_t* QoS_Flow_Id = &QoS_flow_Item->ran_param_struct.ran_param_struct[0];
  QoS_Flow_Id->ran_param_id = QOS_FLOW_IDENTIFIER_8_4_4_1;
  QoS_Flow_Id->ran_param_val.type = ELEMENT_KEY_FLAG_TRUE_RAN_PARAMETER_VAL_TYPE;
  QoS_Flow_Id->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(QoS_Flow_Id->ran_param_val.flag_false != NULL && "Memory exhausted");
  QoS_Flow_Id->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  char QFI_str[] = "10";
  byte_array_t QFI = cp_str_to_ba(QFI_str);
  QoS_Flow_Id->ran_param_val.flag_false->octet_str_ran.len = QFI.len;
  QoS_Flow_Id->ran_param_val.flag_false->octet_str_ran.buf = QFI.buf;

  return;
}

static void gen_List_of_Secondary_cells_to_be_setup(seq_ran_param_t* List_num_2ndCells) 
{
  int num_2ndCells = 1;
  // List of Secondary cells to be setup, LIST (len 1)
  List_num_2ndCells->ran_param_id = LIST_OF_SECONDARY_CELLS_TO_BE_SETUP_8_4_4_1;
  List_num_2ndCells->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  List_num_2ndCells->ran_param_val.lst = calloc(1, sizeof(ran_param_list_t));
  assert(List_num_2ndCells->ran_param_val.lst != NULL && "Memory exhausted");
  List_num_2ndCells->ran_param_val.lst->sz_lst_ran_param = num_2ndCells;
  List_num_2ndCells->ran_param_val.lst->lst_ran_param = calloc(num_2ndCells, sizeof(lst_ran_param_t));
  assert(List_num_2ndCells->ran_param_val.lst->lst_ran_param != NULL && "Memory exhausted");

  // >Secondary cell Item to be setup, STRUCTURE (len 1)
  lst_ran_param_t* secCell_item = &List_num_2ndCells->ran_param_val.lst->lst_ran_param[0];

  secCell_item->ran_param_struct.sz_ran_param_struct = 1;
  secCell_item->ran_param_struct.ran_param_struct = calloc(1, sizeof(seq_ran_param_t));
  assert(secCell_item->ran_param_struct.ran_param_struct != NULL && "Memory exhausted");

  // >>Secondary cell ID, ELEMENT
  seq_ran_param_t* secCell_Id = &secCell_item->ran_param_struct.ran_param_struct[0];
  secCell_Id->ran_param_id = SECONDARY_CELL_ID_8_4_4_1;
  secCell_Id->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
  secCell_Id->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
  assert(secCell_Id->ran_param_val.flag_false != NULL && "Memory exhausted");
  secCell_Id->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  char cellID_str[] = "0";
  byte_array_t QFI = cp_str_to_ba(cellID_str);
  secCell_Id->ran_param_val.flag_false->octet_str_ran.len = QFI.len;
  secCell_Id->ran_param_val.flag_false->octet_str_ran.buf = QFI.buf;

  return;
}

static e2sm_rc_ctrl_msg_frmt_1_t gen_rc_ctrl_msg_frmt_1_Handover_Control(char targetcell) 
{
  e2sm_rc_ctrl_msg_frmt_1_t dst = {0};
  // 8.4.4.1

  // Target Primary Cell ID, STRUCTURE (len 1)
  // > CHOICE Target Cell, STRUCTURE (len 2)
  // >>  NR Cell, STRUCTURE (len 1))
  // >>> NR CGI, ELEMENT
  // >>E-UTRA Cell, STRUCTURE (len 1)
  // >>>E-UTRA CGI, ELEMENT

  // List of PDU sessions for handover, LIST (len 1)
  // >PDU session Item for handover, STRUCTURE (len 2)
  // >>PDU Session ID, ELEMENT
  // >>List of QoS flows in the PDU session, LIST (len 1)
  // >>>QoS flow Item, STRUCTURE (len 1)
  // >>>>QoS Flow Identifier, ELEMENT

  // List of DRBs for handover, LIST (len 1)
  // > DRB item for handover, STRUCTURE (len 2)
  // >> DRB ID, ELEMENT
  // >> List of QoS flows in the DRB, LIST (len 1)
  // >>> QoS flow Item, STRUCTURE (len 1)
  // >>>> QoS flow Identifier, ELEMENT

  // List of Secondary cells to be setup, LIST (len 1)
  // >Secondary cell Item to be setup, STRUCTURE (len 1)
  // >>Secondary cell ID, ELEMENT

  dst.sz_ran_param = 4;
  dst.ran_param = calloc(4, sizeof(seq_ran_param_t));
  assert(dst.ran_param != NULL && "Memory exhausted");

  gen_Target_Primary_Cell_ID(&dst.ran_param[0], targetcell);
  gen_List_of_PDU_sessions_for_handover(&dst.ran_param[1]);
  gen_List_of_DRBs_for_handover(&dst.ran_param[2]);
  gen_List_of_Secondary_cells_to_be_setup(&dst.ran_param[3]);

  return dst;
}

static e2sm_rc_ctrl_msg_frmt_1_t gen_rc_ctrl_msg_frmt_1_cell_trigger(char targetcell) 
{
  e2sm_rc_ctrl_msg_frmt_1_t dst = {0};
  // 8.4.4.1

  // Target Primary Cell ID, STRUCTURE (len 1)
  // > CHOICE Target Cell, STRUCTURE (len 2)
  // >>  NR Cell, STRUCTURE (len 1))
  // >>> NR CGI, ELEMENT
  // >>E-UTRA Cell, STRUCTURE (len 1)
  // >>>E-UTRA CGI, ELEMENT
  dst.sz_ran_param = 1;
  dst.ran_param = calloc(4, sizeof(seq_ran_param_t));
  assert(dst.ran_param != NULL && "Memory exhausted");

  gen_Target_Primary_Cell_ID(&dst.ran_param[0], targetcell);
  return dst;
}

static e2sm_rc_ctrl_msg_t gen_handover_rc_ctrl_msg(e2sm_rc_ctrl_msg_e msg_frmt, uint8_t targetcell) 
{
  e2sm_rc_ctrl_msg_t dst = {0};

  if (msg_frmt == FORMAT_1_E2SM_RC_CTRL_MSG) 
  {
    dst.format = msg_frmt;
    dst.frmt_1 = gen_rc_ctrl_msg_frmt_1_Handover_Control(targetcell);
  } else 
  {
    assert(0 != 0 && "not implemented the fill func for this ctrl msg frmt");
  }

  return dst;
}

static e2sm_rc_ctrl_msg_t gen_cell_trigger_rc_ctrl_msg(e2sm_rc_ctrl_msg_e msg_frmt, char targetcell) 
{
  e2sm_rc_ctrl_msg_t dst = {0};

  if (msg_frmt == FORMAT_1_E2SM_RC_CTRL_MSG) {
    dst.format = msg_frmt;
    dst.frmt_1 = gen_rc_ctrl_msg_frmt_1_cell_trigger(targetcell);
  } else 
  {
    assert(0 != 0 && "not implemented the fill func for this ctrl msg frmt");
  }

  return dst;
}

static ue_id_e2sm_t gen_rc_ue_id(ue_id_e2sm_e type, int ueid) 
{
  ue_id_e2sm_t ue_id = {0};
  if (type == GNB_UE_ID_E2SM) 
  {
    ue_id.type = GNB_UE_ID_E2SM;
    ue_id.gnb.ran_ue_id = (uint64_t*)malloc(sizeof(uint64_t));
    *(ue_id.gnb.ran_ue_id) = ueid;
  } else 
  {
    assert(0 != 0 && "not supported UE ID type");
  }
  return ue_id;
}

static bool eq_sm(sm_ran_function_t const* elem, int const id) 
{
  if (elem->id == id)
    return true;

  return false;
}

static size_t recount_connected_ues(struct SINR_Map const* cell)
{
  if (cell == NULL || cell->connectedUEs == NULL)
    return 0;

  size_t count = 0;
  for (int j = 0; j < MAX_REGISTERED_UES; ++j) {
    if (cell->connectedUEs[j].is_available)
      count++;
  }
  return count;
}

static bool cell_has_low_load(struct SINR_Map const* cell)
{
  if (cell == NULL || cell->is_off || !cell->is_running)
    return false;

  return (int)recount_connected_ues(cell) <= g_policy.max_ues_for_shutdown;
}

static void clear_cell_ue_runtime_data(struct SINR_Map* cell)
{
  if (cell == NULL || cell->connectedUEs == NULL)
    return;

  for (int j = 0; j < MAX_REGISTERED_UES; ++j) {
    if (cell->connectedUEs[j].neighCells != NULL) {
      free(cell->connectedUEs[j].neighCells);
      cell->connectedUEs[j].neighCells = NULL;
    }
  }

  free(cell->connectedUEs);
  cell->connectedUEs = NULL;
  cell->numOfConnectedUEs = 0;
}

static void cancel_shutdown(struct SINR_Map* cell, double now, const char* reason)
{
  decision_log("deny", cell->cellID, 0, cell->numOfConnectedUEs, 0.0, 0, 0.0, reason);
  cell->pending_shutdown = false;
  cell->stable_low_load_reports = 0;
  cell->last_state_change_time = now;
  if (cell->connectedUEs != NULL) {
    for (int j = 1; j < MAX_REGISTERED_UES; ++j) {
      cell->connectedUEs[j].handover_in_progress = false;
      cell->connectedUEs[j].last_target_cell = 0;
    }
  }
}

void forEachCell(Callback targetCellFinding, Callback cbHOAction, Callback cbSwitchOffAction, callback_data_t data)
{
  const double now = policy_now();
  struct SINR_Map* candidate = NULL;
  uint16_t anchor = 0;
  for (int i = 1; i < MAX_REGISTERED_CELLS; ++i) {
    struct SINR_Map* cell = cells_sinr_map[i].sinrMap;
    if (cell == NULL || cell->is_off || !cell->is_running)
      continue;
    if (anchor == 0)
      anchor = cell->cellID;
    cell->numOfConnectedUEs = recount_connected_ues(cell);
    if (cell->pending_shutdown)
      candidate = cell;
  }

  // Serialize relocations: other cells remain eligible targets and their
  // capacity includes outstanding incoming handovers. Keep one serving anchor.
  if (candidate == NULL) {
    for (int i = 1; i < MAX_REGISTERED_CELLS; ++i) {
      struct SINR_Map* cell = cells_sinr_map[i].sinrMap;
      if (cell == NULL || cell->cellID == anchor || cell->cellID > 9 ||
          cell->cellID == (uint16_t)g_policy.protected_cell_id ||
          pending_incoming_ues(cell->cellID) > 0)
        continue;
      if (now - cell->last_kpm_update_time > g_policy.kpm_max_age_sec) {
        cell->stable_low_load_reports = 0;
        continue;
      }
      // The same cached KPM sample cannot be counted repeatedly as stability.
      if (cell->last_kpm_sequence <= cell->last_evaluated_sequence)
        continue;
      cell->last_evaluated_sequence = cell->last_kpm_sequence;
      const bool cooldown_ok = cell->last_state_change_time == 0 ||
          now - cell->last_state_change_time >= g_policy.cell_switch_cooldown_sec;
      if (cell_has_low_load(cell) && cooldown_ok)
        cell->stable_low_load_reports++;
      else
        cell->stable_low_load_reports = 0;
      if (cell->stable_low_load_reports >= g_policy.required_stable_low_load_reports) {
        candidate = cell;
        cell->pending_shutdown = true;
        cell->shutdown_start_time = now;
        cell->confirmed_handover_count = 0;
        cell->last_confirmation_time = 0;
        decision_log("candidate", cell->cellID, 0, cell->numOfConnectedUEs, 0.0, 0, 0.0, "fresh_stable_low_load");
        break;
      }
    }
  }
  if (candidate == NULL)
    return;
  struct SINR_Map* cell = candidate;
  if (now - cell->shutdown_start_time > g_policy.shutdown_timeout_sec) {
    // Timing out is failure to prove relocation, never permission to power off.
    cancel_shutdown(cell, now, "handover_confirmation_timeout");
    return;
  }

  int representative_ue_id = 0;
  uint16_t planned_targets[MAX_REGISTERED_UES] = {0};
  size_t reserved[MAX_REGISTERED_CELLS] = {0};
  // Validate the whole relocation before sending the first control message.
  for (int j = 1; j < MAX_REGISTERED_UES; ++j) {
    if (cell->connectedUEs == NULL)
      break;
    struct SINRServingValues* ue = &cell->connectedUEs[j];
    if (ue->last_target_cell != 0) {
      representative_ue_id = ue->ueID;
      if (!ue->is_available) {
        struct SINR_Map* target = cells_sinr_map[ue->last_target_cell].sinrMap;
        struct SINRServingValues* relocated = get_UE(ue->last_target_cell, ue->ueID);
        if (target == NULL || target->is_off || target->pending_shutdown || relocated == NULL ||
            now - relocated->last_serving_update_time > g_policy.kpm_max_age_sec) {
          cancel_shutdown(cell, now, "relocated_ue_not_confirmed_on_active_target");
          return;
        }
      }
    }
    if (!ue->is_available || ue->handover_in_progress)
      continue;
    representative_ue_id = ue->ueID;
    if (now - ue->last_serving_update_time > g_policy.kpm_max_age_sec ||
        ue->neighCells == NULL || ue->numOfNeighCells == 0) {
      cancel_shutdown(cell, now, "missing_fresh_serving_or_neighbour_report");
      return;
    }
    callback_data_t ue_data = {
      .nodes = data.nodes, .neighCells = ue->neighCells, .ueID = ue->ueID,
      .frmCurntCell = cell->cellID, .servingSinr = ue->sinr
    };
    const uint16_t target = targetCellFinding(ue_data);
    if (target == 0) {
      cancel_shutdown(cell, now, "no_safe_target_cell");
      return;
    }
    struct SINR_Map* destination = cells_sinr_map[target].sinrMap;
    if (destination == NULL || destination->numOfConnectedUEs + pending_incoming_ues(target) + reserved[target] >= (size_t)g_policy.max_ues_on_target) {
      cancel_shutdown(cell, now, "planned_target_capacity_exceeded");
      return;
    }
    planned_targets[j] = target;
    reserved[target]++;
  }
  for (int j = 1; j < MAX_REGISTERED_UES; ++j) {
    const uint16_t target = planned_targets[j];
    if (target == 0)
      continue;
    struct SINRServingValues* ue = &cell->connectedUEs[j];
    callback_data_t ue_data = {
      .nodes = data.nodes, .neighCells = ue->neighCells, .ueID = ue->ueID,
      .frmCurntCell = cell->cellID, .servingSinr = ue->sinr,
      .toTargetCell = target, .targetSinr = ue->neighCells[target].sinr
    };
    if (!cbHOAction(ue_data)) {
      cancel_shutdown(cell, now, "handover_command_failed");
      return;
    }
    ue->handover_in_progress = true;
    ue->last_target_cell = target;
    ue->last_handover_time = now;
    decision_log("handover_sent", cell->cellID, ue->ueID, cell->numOfConnectedUEs,
                 ue->sinr, target, ue_data.targetSinr, "await_serving_report_from_target");
  }

  // Every original UE must have appeared at its destination. Also wait a
  // confirmation interval after the most recent relocation, not after request.
  cell->numOfConnectedUEs = recount_connected_ues(cell);
  if (cell->numOfConnectedUEs != 0 || cell->confirmed_handover_count == 0 ||
      now - cell->last_confirmation_time < g_policy.shutdown_confirmation_sec)
    return;
  data.frmCurntCell = cell->cellID;
  data.ueID = representative_ue_id;
  if (!cbSwitchOffAction(data)) {
    cancel_shutdown(cell, now, "rc_control_failed");
    return;
  }
  decision_log("switch_off", cell->cellID, representative_ue_id, 0, 0.0, 0, 0.0,
               "all_handovers_confirmed_control_submitted");
  cell->last_state_change_time = now;
  cell->pending_shutdown = false;
  cell->stable_low_load_reports = 0;
  cell->is_running = false;
  cell->is_off = true;
  clear_cell_ue_runtime_data(cell);
}


// void doHandoverAction(const e2_node_arr_xapp_t * nodes, const int ueID, const uint8_t frmCurntCell, const uint8_t
// toTargetCell) {
uint16_t doHandoverAction(callback_data_t data) 
{
  char trgtCell = '0' + data.toTargetCell;
  printf("[xApp]: data.toTargetCell= %d ..\n", data.toTargetCell);

  if (!(trgtCell > '0' && trgtCell <= '9')) 
  {
    printf("[xApp]: Invalid target cell %c\n", trgtCell);
    return 0;
  }

  if (g_policy.dry_run) {
    printf("[xApp][DRY-RUN]: would handover UE %d from cell %d to cell %d\n",
           data.ueID, data.frmCurntCell, data.toTargetCell);
    return 1;
  }

  rc_ctrl_req_data_t rc_ctrl = {0};
  ue_id_e2sm_t ue_id_1 = gen_rc_ue_id(GNB_UE_ID_E2SM, data.ueID);

  rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id_1, CONNECTED_MODE_MOBILITY,
                                HANDOVER_CONTROL_7_6_4_1);
  rc_ctrl.msg = gen_handover_rc_ctrl_msg(FORMAT_1_E2SM_RC_CTRL_MSG, trgtCell);

  printf("[xApp]: Send Handover Control message to move IMSI %d from cellId %d to target cellId %c \n", data.ueID,
         data.frmCurntCell, trgtCell);

  bool handover_sent = false;
  for (size_t i = 0; i < (*data.nodes).len; ++i) 
  {
    // This backend implements NR handover control in MmWaveEnbNetDevice.
    if ((*data.nodes).n[i].id.type != ngran_gNB)
      continue;
    sm_ans_xapp_t ans = control_sm_xapp_api(&(*data.nodes).n[i].id, SM_RC_ID, &rc_ctrl);

    if (ans.success) 
    {
      handover_sent = true;
      printf("[xApp]: Handover request sent successfully to node %zu\n", i);
    }
  }

  free_rc_ctrl_req_data(&rc_ctrl);
  return handover_sent ? 1 : 0;
}

static global_e2_node_id_t* energy_control_node(e2_node_arr_xapp_t const* nodes)
{
  if (nodes == NULL)
    return NULL;
  for (size_t i = 0; i < nodes->len; ++i)
    if (nodes->n[i].id.type == ngran_gNB)
      return &nodes->n[i].id;
  return NULL;
}

uint16_t switchOffCurrentCell(callback_data_t data) 
{
  if (g_policy.dry_run) {
    printf("[xApp][DRY-RUN]: would switch off cell %d\n", data.frmCurntCell);
    return 1;
  }

  if (data.frmCurntCell == 0 || data.frmCurntCell > 9) {
    printf("[xApp]: Invalid cell id %d for switch-off message format\n", data.frmCurntCell);
    return 0;
  }

  rc_ctrl_req_data_t rc_ctrl = {0};
  ue_id_e2sm_t ue_id = gen_rc_ue_id(GNB_UE_ID_E2SM, data.ueID);

  rc_ctrl.hdr = gen_rc_ctrl_hdr(FORMAT_1_E2SM_RC_CTRL_HDR, ue_id, ENERGY_STATE, CELL_OFF);
  char frmCurentCell = '0' + data.frmCurntCell;
  rc_ctrl.msg = gen_cell_trigger_rc_ctrl_msg(FORMAT_1_E2SM_RC_CTRL_MSG, frmCurentCell);

  printf("[xApp]: Send switch off Control message to switch off cell %d\n", data.frmCurntCell);
  // The first E2 node may be the LTE anchor, which has no Energy_state handler.
  global_e2_node_id_t* recipient = energy_control_node(data.nodes);
  if (recipient == NULL) {
    free_rc_ctrl_req_data(&rc_ctrl);
    return 0;
  }
  sm_ans_xapp_t ans = control_sm_xapp_api(recipient, SM_RC_ID, &rc_ctrl);

  free_rc_ctrl_req_data(&rc_ctrl);

  if (!ans.success) {
    printf("[xApp]: switch-off RC control failed for cell %d\n", data.frmCurntCell);
    return 0;
  }

  return 1;
}

int main(int argc, char* argv[]) 
{
  load_policy_from_env();
  open_decision_log();

  fr_args_t args = init_fr_args(argc, argv);
  init_xapp_api(&args);

  e2_node_arr_xapp_t nodes = {0};
  for (int attempt = 0; attempt < g_policy.wait_for_e2_sec; ++attempt) {
    nodes = e2_nodes_xapp_api();
    if (nodes.len > 0)
      break;

    printf("[xApp]: waiting for E2 node... (%d/%d)\n", attempt + 1, g_policy.wait_for_e2_sec);
    sleep(1);
  }
  defer({ free_e2_node_arr_xapp(&nodes); });
  assert(nodes.len > 0 && "No E2 nodes available. Start nearRT-RIC and ns-3 scenario before the xApp.");
  printf("Connected E2 nodes = %d\n", nodes.len);

  pthread_mutexattr_t attr = {0};
  int rc = pthread_mutex_init(&mtx, &attr);
  assert(rc == 0);

  // Start KPM subscription
  sm_ans_xapp_t* hndl = calloc(nodes.len, sizeof(sm_ans_xapp_t));
  assert(hndl != NULL);

  int const KPM_ran_function = 2;
  for (size_t i = 0; i < nodes.len; ++i) 
  {
    e2_node_connected_xapp_t* n = &nodes.n[i];
    size_t const idx = find_sm_idx(n->rf, n->len_rf, eq_sm, KPM_ran_function);

    if (n->rf[idx].defn.kpm.ric_report_style_list != NULL) 
    {
      kpm_sub_data_t kpm_sub = gen_kpm_subs(&n->rf[idx].defn.kpm);
      hndl[i] = report_sm_xapp_api(&n->id, KPM_ran_function, &kpm_sub, sm_cb_kpm);
      assert(hndl[i].success == true);
      free_kpm_sub_data(&kpm_sub);
    }
  }

  // Wait for enough KPM measurements
  printf("Waiting for KPM measurements...\n");
  sleep(g_policy.decision_period_sec);  // Wait for initial KPM measurement cycles

  // Check measurements and trigger handovers
  callback_data_t context = {.nodes = &nodes};

  double last_decision_time = -1.0;
  while (1) {
    usleep(100000);


    // Lock to ensure measurement data is consistent
    pthread_mutex_lock(&mtx);

    // Repeated polling cannot advance the policy while ns-3 is computing.
    if (simulation_time_s - last_decision_time < g_policy.decision_period_sec) {
      pthread_mutex_unlock(&mtx);
      continue;
    }
    last_decision_time = simulation_time_s;
    // Check cells and trigger handovers
    forEachCell(getTargetCellID, doHandoverAction, switchOffCurrentCell, context);

    pthread_mutex_unlock(&mtx);

    // Wait before next check
  }

  // Cleanup
  for (int i = 0; i < nodes.len; ++i) 
  {
    if (hndl[i].success == true)
      rm_report_sm_xapp_api(hndl[i].u.handle);
  }
  free(hndl);

  while (try_stop_xapp_api() == false)
    usleep(1000);

  rc = pthread_mutex_destroy(&mtx);
  assert(rc == 0);

  if (g_decision_log != NULL) {
    fclose(g_decision_log);
    g_decision_log = NULL;
  }

  printf("xApp completed successfully\n");
  return 0;
}
