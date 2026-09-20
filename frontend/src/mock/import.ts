import type { ImportResponse, SchedulerConfig } from '../types';
import csvCleanJson from './import_csv_clean.json';
import csvViolationJson from './import_csv_violation.json';
import imageJson from './import_image.json';
import { defaultConfig } from '../config/defaults';
import { metaFromConfig, minRequiredFor } from '../config/derive';
import { validateSlotsWithConfig } from './configValidator';
import { normalizeImportedSlots } from '../lib/schedule';

/** JSON 里只放解析结果，体检结论在运行时用离线校验器算，保证与应用后的看板完全一致 */
type ImportFixture = Omit<ImportResponse, 'validation' | 'soft_metrics'>;

export const mockImportClean = csvCleanJson as unknown as ImportFixture;
export const mockImportViolation = csvViolationJson as unknown as ImportFixture;
export const mockImportImage = imageJson as unknown as ImportFixture;

const IMAGE_EXT = /\.(png|jpe?g|webp)$/i;
const SHEET_EXT = /\.(csv|tsv|xlsx|xls)$/i;

/** 与契约第 3 节的限制对齐，mock 模式也要能演示「超限被拒」 */
export const IMPORT_MAX_BYTES = 5 * 1024 * 1024;
export const IMPORT_ACCEPT = '.csv,.tsv,.xlsx,.xls,.png,.jpg,.jpeg,.webp';

export function isImageFile(name: string): boolean {
  return IMAGE_EXT.test(name);
}

export function isSupportedFile(name: string): boolean {
  return IMAGE_EXT.test(name) || SHEET_EXT.test(name);
}

/**
 * mock 场景按文件名决定，这样演示站的「样例文件」按钮走的是和真实拖拽完全相同的链路：
 * 图片 → 视觉识别（缺格 + 未匹配）；文件名含「违规」→ 体检发现违规；其余 → 全通过。
 */
export function resolveImportFixture(fileName: string): ImportFixture {
  if (isImageFile(fileName)) return mockImportImage;
  if (/违规|violat/i.test(fileName)) return mockImportViolation;
  return mockImportClean;
}

/**
 * 导入的 mock。fixture 里的日期/班次是老键（周一 / 早班），归一时靠
 * normalizeImportedSlots 的别名匹配落到配置维度上——真实后端拿到人写的「周一」表头时
 * 也要做同样的事，所以这层别名兜底前端必须保留。
 */
export function mockImport(
  file: File,
  visionModel?: string | null,
  config?: SchedulerConfig | null,
): ImportResponse {
  const cfg = config ?? defaultConfig();
  const meta = metaFromConfig(cfg);
  const fixture = resolveImportFixture(file.name);
  const body = JSON.parse(JSON.stringify(fixture)) as ImportFixture;
  const slots = normalizeImportedSlots(meta, body.slots, (day, shift) =>
    minRequiredFor(cfg, day, shift),
  );
  const { validation, soft_metrics } = validateSlotsWithConfig(cfg, slots);
  const expected = cfg.scenario.days.length * cfg.scenario.shifts.length;
  return {
    ...body,
    slots,
    // 格子总数期望值由配置推导，不再写死 14（契约 5.5）
    stats: body.stats
      ? { ...body.stats, slots_expected: expected, slots_found: slots.length }
      : body.stats,
    // 视觉模型由用户在选择器里指定，mock 也要如实回显，否则选择器看着像没生效
    model_used: body.extractor === 'vision_llm' ? (visionModel ?? body.model_used) : body.model_used,
    validation,
    soft_metrics,
  };
}

/**
 * 演示站没有后端提供 `/api/import/template`，用同结构的常量兜住模板下载入口。
 *
 * 这份表格的内容与 `import_csv_clean.json` 完全一致，而且**遵守员工档案里的不可用时段** ——
 * 模板一旦排了一个当天请假的人，用户导入后会立刻看到一屏 R-08 违规，
 * 然后开始怀疑是自己下错了文件。
 */
export const MOCK_TEMPLATE_CSV = [
  '# 智能排班助手 · 导入模板（长表）。删掉本注释行也能正常导入',
  '# 员工列可用空格 / 逗号 / 顿号分隔；工号写 E01 或 1 都能识别，超出员工名单的会被列为未匹配',
  '日期,班次,员工',
  '周一,早班,E01 E03 E06 E08 E11',
  '周一,晚班,E02 E07 E09 E12 E18',
  '周二,早班,E01 E03 E08 E17 E20',
  '周二,晚班,E02 E07 E10 E12 E18',
  '周三,早班,E03 E06 E08 E11 E17',
  '周三,晚班,E04 E07 E09 E12 E16',
  '周四,早班,E01 E03 E06 E11 E17',
  '周四,晚班,E02 E07 E10 E12 E18',
  '周五,早班,E04 E08 E11 E17 E20',
  '周五,晚班,E02 E05 E06 E09 E12',
  '周六,早班,E01 E10 E13 E15 E16 E17',
  '周六,晚班,E05 E08 E11 E14 E19 E20',
  '周日,早班,E01 E04 E10 E13 E15 E16',
  '周日,晚班,E02 E06 E07 E09 E14 E19',
].join('\n');

/**
 * 「有违规的 CSV」样例的文件内容，与 `import_csv_violation.json` 的解析结果一致：
 * 矩阵排版 + 行尾多一个空列（对应 fixture 里的那条解析告警），
 * 违规也都是**店长自己排出来的那几处**（周六早班少一人、E02 周二连上两班且间隔不足、E02 超周上限）。
 */
export const MOCK_VIOLATION_CSV = [
  '日期,早班,晚班,',
  '周一,E01 E03 E06 E08 E11,E02 E07 E09 E12 E18,',
  '周二,E01 E02 E03 E08 E17 E20,E02 E07 E10 E12 E18,',
  '周三,E03 E06 E08 E11 E17 E20,E04 E07 E09 E12 E16,',
  '周四,E01 E03 E06 E11 E17,E02 E07 E10 E12 E18,',
  '周五,E04 E08 E11 E17 E20,E02 E05 E06 E09 E12,',
  '周六,E01 E10 E13 E15 E16,E05 E08 E11 E14 E19 E20,',
  '周日,E01 E04 E10 E13 E15 E16,E02 E06 E07 E09 E14 E19,',
].join('\n');

/** 演示样例文件：静态站上用户手边没有排班表，直接给三个可点的场景 */
export const MOCK_SAMPLE_FILES: Array<{ label: string; hint: string; build: () => File }> = [
  {
    label: '干净 CSV',
    hint: '格子全解析，体检全部通过',
    build: () => new File([MOCK_TEMPLATE_CSV], '门店排班表-下周.csv', { type: 'text/csv' }),
  },
  {
    label: '有违规的 CSV',
    hint: '解析成功但体检查出硬规则违规',
    build: () => new File([MOCK_VIOLATION_CSV], '门店排班表-违规样例.csv', { type: 'text/csv' }),
  },
  {
    label: '排班表图片',
    hint: '视觉识别约 10 秒，含缺格与未匹配项',
    build: () => new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], '排班表拍照.png', { type: 'image/png' }),
  },
];
