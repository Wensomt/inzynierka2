// Compiled with the real FlexRIC headers and libraries by check_xapp_policy.py.
#define main xapp_original_main
#include "my_energy_policy_source.c"
#undef main

static int commands, power_commands;
static uint16_t first_only(callback_data_t d) { return d.ueID == 1 ? 2 : 0; }
static uint16_t target_two(callback_data_t d) { (void)d; return 2; }
static uint16_t handover(callback_data_t d) { (void)d; commands++; return 1; }
static uint16_t power_off(callback_data_t d) { (void)d; power_commands++; return 1; }

static void reset_test(void)
{
  for (int i = 1; i < MAX_REGISTERED_CELLS; ++i) {
    if (cells_sinr_map[i].sinrMap) {
      clear_cell_ue_runtime_data(cells_sinr_map[i].sinrMap);
      free(cells_sinr_map[i].sinrMap);
    }
  }
  memset(cells_sinr_map, 0, sizeof(cells_sinr_map));
  simulation_time_s = 10;
  kpm_sequence++;
  commands = power_commands = 0;
  g_policy.required_stable_low_load_reports = 1;
  g_policy.max_ues_for_shutdown = 4;
  g_policy.max_ues_on_target = 64;
  g_policy.shutdown_confirmation_sec = 1;
  g_policy.shutdown_timeout_sec = 30;
  g_policy.cell_switch_cooldown_sec = 0;
  add_SINR(2);
  struct SINR_Map* source = add_SINR(3);
  add_UE(source, 1, 10);
  add_UE(source, 2, 10);
  add_neighCell(get_UE(3, 1), 2, 12);
  add_neighCell(get_UE(3, 2), 2, 12);
}

int main(void)
{
  e2_node_connected_xapp_t connected[2] = {0};
  connected[0].id.type = ngran_eNB;
  connected[1].id.type = ngran_gNB;
  e2_node_arr_xapp_t nodes = {.len = 2, .n = connected};
  assert(energy_control_node(&nodes) == &connected[1].id);
  nodes.len = 1;
  assert(energy_control_node(&nodes) == NULL);
  callback_data_t context = {0};
  reset_test();
  forEachCell(first_only, handover, power_off, context);
  assert(commands == 0 && power_commands == 0); // One unsafe UE cancels the whole plan.

  reset_test();
  for (int id = 3; id < 66; ++id)
    add_UE(cells_sinr_map[2].sinrMap, id, 15);
  forEachCell(target_two, handover, power_off, context);
  assert(commands == 0); // Two planned relocations cannot take 63 UE past capacity 64.

  reset_test();
  forEachCell(target_two, handover, power_off, context);
  assert(commands == 2 && power_commands == 0);
  simulation_time_s = 10.5;
  add_UE(cells_sinr_map[2].sinrMap, 1, 12);
  add_UE(cells_sinr_map[2].sinrMap, 2, 12);
  forEachCell(target_two, handover, power_off, context);
  assert(power_commands == 0); // Positive serving reports still require the confirmation interval.
  simulation_time_s = 11.6;
  forEachCell(target_two, handover, power_off, context);
  assert(power_commands == 1 && cells_sinr_map[3].sinrMap->is_off);

  reset_test();
  forEachCell(target_two, handover, power_off, context);
  simulation_time_s = 41;
  forEachCell(target_two, handover, power_off, context);
  assert(commands == 2 && power_commands == 0); // Missing confirmation never permits power off.
  puts("My Energy policy checks: 5 passed");
  reset_test();
  return 0;
}
