import type { ConfigIssue } from '../types';
import { whereEmployee, whereRule } from '../types';
import type { FormScope } from './checks';

/**
 * 自检结论 → 该去哪一步修。
 *
 * 单独成文件是因为有两处要用同一套判断：配置页的自检面板，和生成页那条「生成被拦下」的横幅。
 * 横幅上的「去『规则』处理」必须和面板里的按钮跳到同一个地方，否则同一条错误在两个入口
 * 指向不同的步骤，用户会以为是两个问题。
 *
 * 先看 where，再按 code 兜底：后端加了新 code 而前端没跟上时，只要它带了 rule_id / employee_id
 * 就仍然能跳对地方；实在认不出来就不给跳转，也比跳到一个改不了这件事的步骤好。
 */

export const SCOPE_LABEL: Record<FormScope, string> = {
  scenario: '排班场景',
  employees: '员工',
  rules: '规则',
};

export function scopeOfIssue(issue: ConfigIssue): FormScope | null {
  if (whereRule(issue.where)) return 'rules';
  if (whereEmployee(issue.where)) return 'employees';
  switch (issue.code) {
    case 'empty_scenario':
    case 'scenario_too_large':
    case 'duplicate_day_id':
    case 'duplicate_shift_id':
    case 'overnight_shift':
      return 'scenario';
    case 'no_employees':
    case 'duplicate_employee_id':
    case 'unknown_skill':
    case 'unknown_unavailable_ref':
    case 'unknown_preferred_shift':
    case 'inactive_employees':
    case 'unused_skill':
      return 'employees';
    case 'supply_lt_demand':
    case 'slot_no_headroom':
    case 'capacity_lt_total_demand':
    // 「当天排不开 / 当天零冗余」的出路基本都在规则页：降下限、或关掉「每人每天最多一个班」
    case 'daily_capacity_lt_demand':
    case 'daily_attribute_capacity_lt_demand':
    case 'no_daily_headroom':
    case 'attribute_absent':
    case 'attribute_supply_lt_demand':
    case 'duplicate_rule_id':
    case 'unknown_rule_type':
    case 'invalid_rule_params':
    case 'locked_rule_forced':
    case 'no_active_business_rules':
    case 'no_min_staff_rule':
    case 'no_headroom':
    case 'peak_lt_default':
      return 'rules';
    default:
      return null;
  }
}
