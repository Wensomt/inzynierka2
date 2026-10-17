/*
 * Minimal AI/ML xApp for FlexRIC + ns-O-RAN/RIC-TaaP laboratory tests.
 *
 * Version: Orange KPM compatible variant.
 *
 * Function:
 *   - subscribes to KPM v3 reports through the FlexRIC xApp API,
 *   - reads KPI values observed in Orange/ns-O-RAN scenarios, especially:
 *       DRB.PdcpSduVolumeDl_Filter.UEID,
 *       QosFlow.PdcpPduVolumeDL_Filter.UEID,
 *       TB.TotNbrDl.*,
 *       L3servingSINR3gpp_cell_*_UEID_*,
 *       L3neighSINRListOf_UEID_*_of_Cell_*,
 *       DRB.EstabSucc.5QI.UEID,
 *       DRB.RelActNbr.5QI.UEID,
 *   - runs a lightweight online logistic-regression model,
 *   - logs an advisory ML decision to stdout and to ml_xapp_orange_kpm_decisions.csv.
 *
 * Mode:
 *   Advisory/monitoring. It does not send RC control messages.
 */

#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/util/alg_ds/alg/defer.h"
#include "../../../../src/util/time_now_us.h"
#include "../../../../src/util/alg_ds/ds/lock_guard/lock_guard.h"

#include <assert.h>
#include <math.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define N_FEATURES 6
#define KPI_NAME_BUF 256

typedef struct {
  double load_high;
  double load_low;
  double sinr_low;
  double p_high;
  double p_low;
  double learning_rate;
  int duration_s;
  uint64_t period_ms;
} ml_cfg_t;

typedef struct {
  double w[N_FEATURES];
  unsigned long updates;
} ml_model_t;

typedef struct {
  double dl_volume_sum;
  double tb_dl_sum;
  double serving_sinr_sum;
  double neigh_sum;
  double estab_sum;
  double rel_sum;
  int n_dl_volume;
  int n_tb_dl;
  int n_serving_sinr;
  int n_neigh;
  int n_estab;
  int n_rel;
  int n_any;
} kpi_snapshot_t;

static pthread_mutex_t mtx;
static ml_cfg_t cfg;
static ml_model_t model;
static FILE* csv_file = NULL;

static double env_double(const char* name, double fallback)
{
  const char* v = getenv(name);
  if(v == NULL || strlen(v) == 0)
    return fallback;
  char* end = NULL;
  double out = strtod(v, &end);
  return (end == v) ? fallback : out;
}

static int env_int(const char* name, int fallback)
{
  const char* v = getenv(name);
  if(v == NULL || strlen(v) == 0)
    return fallback;
  char* end = NULL;
  long out = strtol(v, &end, 10);
  return (end == v) ? fallback : (int)out;
}

static uint64_t env_u64(const char* name, uint64_t fallback)
{
  const char* v = getenv(name);
  if(v == NULL || strlen(v) == 0)
    return fallback;
  char* end = NULL;
  unsigned long long out = strtoull(v, &end, 10);
  return (end == v) ? fallback : (uint64_t)out;
}

static double safe_avg(double sum, int n)
{
  return n > 0 ? sum / (double)n : 0.0;
}

static double sigmoid(double z)
{
  if(z > 30.0) return 1.0;
  if(z < -30.0) return 0.0;
  return 1.0 / (1.0 + exp(-z));
}

static double clamp01(double v)
{
  if(v < 0.0) return 0.0;
  if(v > 1.0) return 1.0;
  return v;
}

static int contains_token(const char* name, const char* token)
{
  return name != NULL && token != NULL && strstr(name, token) != NULL;
}

static int is_dl_volume_name(const char* name)
{
  return contains_token(name, "PdcpSduVolumeDl") ||
         contains_token(name, "PdcpSduVolumeDL") ||
         contains_token(name, "PdcpPduVolumeDl") ||
         contains_token(name, "PdcpPduVolumeDL") ||
         contains_token(name, "VolumeDl") ||
         contains_token(name, "VolumeDL") ||
         contains_token(name, "DRB.UEThpDl") ||
         contains_token(name, "UEThpDl") ||
         contains_token(name, "RRU.PrbUsedDl") ||
         contains_token(name, "RRU.PrbTotDl") ||
         contains_token(name, "RRU.PrbAvailDl");
}

static int is_tb_dl_name(const char* name)
{
  return contains_token(name, "TB.TotNbrDl") ||
         contains_token(name, "TotNbrDl");
}

static int is_serving_sinr_name(const char* name)
{
  return contains_token(name, "L3servingSINR") ||
         contains_token(name, "servingSINR");
}

static int is_neigh_name(const char* name)
{
  return contains_token(name, "L3neighSINRListOf") ||
         contains_token(name, "neighSINRList");
}

static int is_estab_name(const char* name)
{
  return contains_token(name, "DRB.EstabSucc") ||
         contains_token(name, "EstabSucc");
}

static int is_rel_name(const char* name)
{
  return contains_token(name, "DRB.RelActNbr") ||
         contains_token(name, "RelActNbr");
}

static void add_metric(kpi_snapshot_t* s, const char* name, double value)
{
  if(s == NULL || name == NULL)
    return;

  s->n_any++;

  if(is_dl_volume_name(name)) {
    s->dl_volume_sum += value;
    s->n_dl_volume++;
  } else if(is_tb_dl_name(name)) {
    s->tb_dl_sum += value;
    s->n_tb_dl++;
  } else if(is_serving_sinr_name(name)) {
    s->serving_sinr_sum += value;
    s->n_serving_sinr++;
  } else if(is_neigh_name(name)) {
    s->neigh_sum += value;
    s->n_neigh++;
  } else if(is_estab_name(name)) {
    s->estab_sum += value;
    s->n_estab++;
  } else if(is_rel_name(name)) {
    s->rel_sum += value;
    s->n_rel++;
  }
}

static void print_kpi_name_once(const char* name, double value)
{
  static unsigned long printed = 0;
  static char seen[256][KPI_NAME_BUF];
  static size_t seen_count = 0;

  const char* env = getenv("ML_XAPP_PRINT_KPI_NAMES");
  int enabled = (env == NULL) ? 1 : atoi(env);
  if(enabled == 0 || name == NULL)
    return;

  for(size_t i = 0; i < seen_count; ++i) {
    if(strcmp(seen[i], name) == 0)
      return;
  }

  if(seen_count < 256) {
    snprintf(seen[seen_count], sizeof(seen[seen_count]), "%s", name);
    seen_count++;
  }

  if(printed < 256) {
    printf("[ML-xApp][KPI-NAME] %s sample=%.6f\n", name, value);
    printed++;
  }
}

static const char* kpm_msg_type_to_str(int t)
{
  switch(t) {
    case FORMAT_1_INDICATION_MESSAGE:
      return "FORMAT_1_INDICATION_MESSAGE";
    case FORMAT_2_INDICATION_MESSAGE:
      return "FORMAT_2_INDICATION_MESSAGE";
    case FORMAT_3_INDICATION_MESSAGE:
      return "FORMAT_3_INDICATION_MESSAGE";
    default:
      return "UNKNOWN_KPM_INDICATION_MESSAGE";
  }
}

static const char* fallback_kpi_name_by_index(size_t idx)
{
  static const char* names[] = {
    "DRB.PdcpSduVolumeDl_Filter.UEID",
    "QosFlow.PdcpPduVolumeDL_Filter.UEID",
    "TB.TotNbrDl.1.UEID",
    "L3servingSINR3gpp",
    "L3neighSINRListOf",
    "DRB.EstabSucc.5QI.UEID",
    "DRB.RelActNbr.5QI.UEID"
  };

  const size_t n = sizeof(names) / sizeof(names[0]);
  return idx < n ? names[idx] : "UNKNOWN_KPI";
}

static void ml_update_and_log(const kpi_snapshot_t* s, int64_t now_us, int64_t latency_us)
{
  const double dl_volume = safe_avg(s->dl_volume_sum, s->n_dl_volume);
  const double tb_dl = safe_avg(s->tb_dl_sum, s->n_tb_dl);
  const double serving_sinr = safe_avg(s->serving_sinr_sum, s->n_serving_sinr);
  const double neigh = safe_avg(s->neigh_sum, s->n_neigh);
  const double estab = safe_avg(s->estab_sum, s->n_estab);
  const double rel = safe_avg(s->rel_sum, s->n_rel);

  if(s->n_dl_volume == 0 && s->n_tb_dl == 0 && s->n_serving_sinr == 0 &&
     s->n_neigh == 0 && s->n_estab == 0 && s->n_rel == 0) {
    printf("[ML-xApp] KPM indication received, but no configured Orange KPI names were matched. total_records=%d\n", s->n_any);
    return;
  }

  /*
   * Feature vector for the laboratory model.
   * This is not a production traffic model. It is a lightweight online model
   * for validating an ML-xApp processing chain in the Orange/ns-O-RAN scenario.
   */
  const double sinr_norm = s->n_serving_sinr > 0 ? clamp01((serving_sinr + 25.0) / 45.0) : 0.5;
  double x[N_FEATURES] = {
    1.0,
    clamp01(dl_volume / 250.0),
    clamp01(tb_dl / 250.0),
    sinr_norm,
    clamp01(neigh / 10.0),
    clamp01((estab + rel) / 10.0)
  };

  double score = 0.0;
  for(size_t i = 0; i < N_FEATURES; ++i)
    score += model.w[i] * x[i];

  const double p_high_load = sigmoid(score);

  /* Weak/self-supervised label derived from available Orange KPM indicators. */
  const bool traffic_high = (dl_volume >= cfg.load_high) || (tb_dl >= cfg.load_high);
  const bool radio_poor = (s->n_serving_sinr > 0) && (serving_sinr <= cfg.sinr_low);
  const double y = (traffic_high || radio_poor) ? 1.0 : 0.0;

  const double err = y - p_high_load;
  for(size_t i = 0; i < N_FEATURES; ++i)
    model.w[i] += cfg.learning_rate * err * x[i];
  model.updates++;

  const char* decision = "KEEP_ACTIVE_NORMAL_LOAD";
  if((dl_volume <= cfg.load_low && tb_dl <= cfg.load_low) && !radio_poor)
    decision = "CANDIDATE_LOW_LOAD_SLEEP";
  else if(radio_poor && neigh > 0.0)
    decision = "CANDIDATE_HANDOVER_REVIEW";
  else if(p_high_load >= cfg.p_high || traffic_high)
    decision = "KEEP_ACTIVE_HIGH_LOAD";

  printf("[ML-xApp] t=%ld us latency=%ld us LOAD_DL=%.2f TB_DL=%.2f SINR=%.2f NEIGH=%.2f ESTAB=%.2f REL=%.2f p_high_load=%.3f decision=%s updates=%lu\n",
         now_us, latency_us, dl_volume, tb_dl, serving_sinr, neigh, estab, rel,
         p_high_load, decision, model.updates);

  if(csv_file != NULL) {
    fprintf(csv_file, "%ld,%ld,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%s,%lu\n",
            now_us, latency_us, dl_volume, tb_dl, serving_sinr, neigh, estab, rel,
            p_high_load, decision, model.updates);
    fflush(csv_file);
  }
}

static void add_kpm_record(kpi_snapshot_t* snap,
                           kpm_ind_msg_format_1_t const* msg_frm_1,
                           size_t data_idx,
                           size_t rec_idx)
{
  if(snap == NULL || msg_frm_1 == NULL)
    return;

  if(data_idx >= msg_frm_1->meas_data_lst_len)
    return;

  if(rec_idx >= msg_frm_1->meas_data_lst[data_idx].meas_record_len)
    return;

  double value = 0.0;
  const int value_type = msg_frm_1->meas_data_lst[data_idx].meas_record_lst[rec_idx].value;

  if(value_type == REAL_MEAS_VALUE) {
    value = msg_frm_1->meas_data_lst[data_idx].meas_record_lst[rec_idx].real_val;
  } else if(value_type == INTEGER_MEAS_VALUE) {
    value = (double)msg_frm_1->meas_data_lst[data_idx].meas_record_lst[rec_idx].int_val;
  } else {
    return;
  }

  char name_buf[KPI_NAME_BUF] = {0};
  const char* name = fallback_kpi_name_by_index(rec_idx);

  if(rec_idx < msg_frm_1->meas_info_lst_len &&
     msg_frm_1->meas_info_lst[rec_idx].meas_type.type == NAME_MEAS_TYPE &&
     msg_frm_1->meas_info_lst[rec_idx].meas_type.name.buf != NULL) {

    size_t len = msg_frm_1->meas_info_lst[rec_idx].meas_type.name.len;
    if(len >= sizeof(name_buf))
      len = sizeof(name_buf) - 1;

    memcpy(name_buf,
           msg_frm_1->meas_info_lst[rec_idx].meas_type.name.buf,
           len);

    name_buf[len] = '\0';

    if(strlen(name_buf) > 0)
      name = name_buf;
  }

  print_kpi_name_once(name, value);
  add_metric(snap, name, value);
}

static void process_kpm_format_1(kpm_ind_msg_format_1_t const* msg_frm_1,
                                 kpi_snapshot_t* snap)
{
  if(msg_frm_1 == NULL || snap == NULL)
    return;

  for(size_t j = 0; j < msg_frm_1->meas_data_lst_len; ++j) {
    for(size_t z = 0; z < msg_frm_1->meas_data_lst[j].meas_record_len; ++z) {
      add_kpm_record(snap, msg_frm_1, j, z);
    }
  }
}

static void sm_cb_kpm(sm_ag_if_rd_t const* rd)
{
  assert(rd != NULL);
  assert(rd->type == INDICATION_MSG_AGENT_IF_ANS_V0);
  assert(rd->ind.type == KPM_STATS_V3_0);

  kpm_ind_data_t const* ind = &rd->ind.kpm.ind;
  kpm_ric_ind_hdr_format_1_t const* hdr = &ind->hdr.kpm_ric_ind_hdr_format_1;

  kpi_snapshot_t snap = {0};
  int64_t const now = time_now_us();
  int64_t const latency = now - hdr->collectStartTime;

  lock_guard(&mtx);

  static unsigned long fmt_print_count = 0;
  if(fmt_print_count < 10) {
    printf("[ML-xApp][KPM-FORMAT] indication_msg_type=%d (%s)\n",
           ind->msg.type,
           kpm_msg_type_to_str(ind->msg.type));
    fmt_print_count++;
  }

  if(ind->msg.type == FORMAT_1_INDICATION_MESSAGE) {

    kpm_ind_msg_format_1_t const* msg_frm_1 = &ind->msg.frm_1;
    process_kpm_format_1(msg_frm_1, &snap);

  } else if(ind->msg.type == FORMAT_3_INDICATION_MESSAGE) {

    kpm_ind_msg_format_3_t const* msg_frm_3 = &ind->msg.frm_3;

    for(size_t i = 0; i < msg_frm_3->ue_meas_report_lst_len; ++i) {
      kpm_ind_msg_format_1_t const* msg_frm_1 =
        &msg_frm_3->meas_report_per_ue[i].ind_msg_format_1;

      process_kpm_format_1(msg_frm_1, &snap);
    }

  } else {

    printf("[ML-xApp] Unsupported KPM indication format: %d (%s)\n",
           ind->msg.type,
           kpm_msg_type_to_str(ind->msg.type));
  }

  ml_update_and_log(&snap, now, latency);
}

static kpm_event_trigger_def_t gen_ev_trig(uint64_t period_ms)
{
  kpm_event_trigger_def_t dst = {0};
  dst.type = FORMAT_1_RIC_EVENT_TRIGGER;
  dst.kpm_ric_event_trigger_format_1.report_period_ms = period_ms;
  return dst;
}

static meas_info_format_1_lst_t gen_meas_info_format_1_lst(const char* action)
{
  meas_info_format_1_lst_t dst = {0};
  dst.meas_type.type = NAME_MEAS_TYPE;
  dst.meas_type.name = cp_str_to_ba(action);
  dst.label_info_lst_len = 1;
  dst.label_info_lst = calloc(1, sizeof(label_info_lst_t));
  assert(dst.label_info_lst != NULL && "Memory exhausted");
  dst.label_info_lst[0].noLabel = calloc(1, sizeof(enum_value_e));
  assert(dst.label_info_lst[0].noLabel != NULL && "Memory exhausted");
  *dst.label_info_lst[0].noLabel = TRUE_ENUM_VALUE;
  return dst;
}

static kpm_act_def_format_1_t gen_act_def_frmt_1(const char** action)
{
  kpm_act_def_format_1_t dst = {0};
  dst.gran_period_ms = cfg.period_ms;

  size_t count = 0;
  while(action[count] != NULL)
    count++;

  dst.meas_info_lst_len = count;
  dst.meas_info_lst = calloc(count, sizeof(meas_info_format_1_lst_t));
  assert(dst.meas_info_lst != NULL && "Memory exhausted");

  for(size_t i = 0; i < count; ++i)
    dst.meas_info_lst[i] = gen_meas_info_format_1_lst(action[i]);

  return dst;
}

static test_info_lst_t filter_cqi_greater_than_zero(void)
{
  test_info_lst_t dst = {0};
  dst.test_cond_type = CQI_TEST_COND_TYPE;
  dst.CQI = TRUE_TEST_COND_TYPE;
  dst.test_cond = calloc(1, sizeof(test_cond_e));
  assert(dst.test_cond != NULL && "Memory exhausted");
  *dst.test_cond = GREATERTHAN_TEST_COND;
  dst.test_cond_value = calloc(1, sizeof(test_cond_value_t));
  assert(dst.test_cond_value != NULL && "Memory exhausted");
  dst.test_cond_value->type = INTEGER_TEST_COND_VALUE;
  dst.test_cond_value->int_value = calloc(1, sizeof(int64_t));
  assert(dst.test_cond_value->int_value != NULL && "Memory exhausted");
  *dst.test_cond_value->int_value = 0;
  return dst;
}

static kpm_act_def_format_4_t gen_act_def_frmt_4(const char** action)
{
  kpm_act_def_format_4_t dst = {0};
  dst.matching_cond_lst_len = 1;
  dst.matching_cond_lst = calloc(1, sizeof(matching_condition_format_4_lst_t));
  assert(dst.matching_cond_lst != NULL && "Memory exhausted");
  dst.matching_cond_lst[0].test_info_lst = filter_cqi_greater_than_zero();
  dst.action_def_format_1 = gen_act_def_frmt_1(action);
  return dst;
}

static kpm_act_def_t gen_act_def(const char** action)
{
  kpm_act_def_t dst = {0};
  dst.type = FORMAT_4_ACTION_DEFINITION;
  dst.frm_4 = gen_act_def_frmt_4(action);
  return dst;
}

int main(int argc, char *argv[])
{
  cfg.load_high = env_double("ML_XAPP_LOAD_HIGH", env_double("ML_XAPP_PRB_HIGH", 70.0));
  cfg.load_low = env_double("ML_XAPP_LOAD_LOW", env_double("ML_XAPP_PRB_LOW", 20.0));
  cfg.sinr_low = env_double("ML_XAPP_SINR_LOW", -10.0);
  cfg.p_high = env_double("ML_XAPP_P_HIGH", 0.70);
  cfg.p_low = env_double("ML_XAPP_P_LOW", 0.30);
  cfg.learning_rate = env_double("ML_XAPP_LR", 0.05);
  cfg.duration_s = env_int("ML_XAPP_DURATION", 120);
  cfg.period_ms = env_u64("ML_XAPP_PERIOD_MS", 1000);

  memset(&model, 0, sizeof(model));

  csv_file = fopen("ml_xapp_orange_kpm_decisions.csv", "a");
  if(csv_file != NULL) {
    fseek(csv_file, 0, SEEK_END);
    if(ftell(csv_file) == 0) {
      fprintf(csv_file, "time_us,latency_us,load_dl,tb_dl,serving_sinr,neighbor_metric,drb_estab,drb_rel,p_high_load,decision,updates\n");
      fflush(csv_file);
    }
  }

  printf("[ML-xApp] Starting. duration=%d s, period=%lu ms, load low/high=%.2f/%.2f, sinr_low=%.2f dB, p low/high=%.2f/%.2f, lr=%.4f\n",
         cfg.duration_s, cfg.period_ms, cfg.load_low, cfg.load_high, cfg.sinr_low,
         cfg.p_low, cfg.p_high, cfg.learning_rate);

  fr_args_t args = init_fr_args(argc, argv);
  init_xapp_api(&args);
  sleep(1);

  e2_node_arr_xapp_t nodes = e2_nodes_xapp_api();
  assert(nodes.len > 0 && "No E2 nodes connected. Start nearRT-RIC and ns-3 scenario first.");
  printf("[ML-xApp] Connected E2 nodes = %d\n", nodes.len);

  pthread_mutexattr_t attr = {0};
  int rc = pthread_mutex_init(&mtx, &attr);
  assert(rc == 0);

  sm_ans_xapp_t* handle = calloc(nodes.len, sizeof(sm_ans_xapp_t));
  assert(handle != NULL && "Memory exhausted");

  const char* act_orange[] = {
    "DRB.PdcpSduVolumeDl_Filter.UEID",
    "QosFlow.PdcpPduVolumeDL_Filter.UEID",
    "TB.TotNbrDl.1.UEID",
    "L3servingSINR3gpp",
    "L3neighSINRListOf",
    "DRB.EstabSucc.5QI.UEID",
    "DRB.RelActNbr.5QI.UEID",
    "RRU.PrbUsedDl",
    "RRU.PrbUsedUl",
    NULL
  };

  for(size_t i = 0; i < nodes.len; ++i) {
    kpm_sub_data_t kpm_sub = {0};
    kpm_sub.ev_trg_def = gen_ev_trig(cfg.period_ms);
    kpm_sub.sz_ad = 1;
    kpm_sub.ad = calloc(1, sizeof(kpm_act_def_t));
    assert(kpm_sub.ad != NULL && "Memory exhausted");
    *kpm_sub.ad = gen_act_def(act_orange);

    const int KPM_RAN_FUNCTION_ID = 2;
    handle[i] = report_sm_xapp_api(&nodes.n[i].id, KPM_RAN_FUNCTION_ID, &kpm_sub, sm_cb_kpm);
    assert(handle[i].success == true && "KPM subscription failed");

    /*
     * Intentionally not calling free_kpm_sub_data(&kpm_sub) here. Some FlexRIC
     * branches use subscription data asynchronously. The leak is negligible for
     * a short laboratory run and avoids use-after-free/double-free issues.
     */
  }

  sleep(cfg.duration_s);

  for(size_t i = 0; i < nodes.len; ++i)
    rm_report_sm_xapp_api(handle[i].u.handle);

  sleep(1);

  while(try_stop_xapp_api() == false)
    usleep(1000);

  if(csv_file != NULL)
    fclose(csv_file);

  free(handle);
  free_e2_node_arr_xapp(&nodes);
  pthread_mutex_destroy(&mtx);

  printf("[ML-xApp] Finished successfully. CSV: ml_xapp_orange_kpm_decisions.csv\n");
  return 0;
}
