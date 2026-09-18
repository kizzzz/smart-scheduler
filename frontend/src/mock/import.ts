import type { ImportResponse, Meta } from '../types';
import metaJson from './meta.json';
import csvCleanJson from './import_csv_clean.json';
import csvViolationJson from './import_csv_violation.json';
import imageJson from './import_image.json';
import { mockValidate } from './validator';
import { normalizeImportedSlots } from '../lib/schedule';

/** JSON 里只放解析结果，体检结论在运行时用离线校验器算，保证与应用后的看板完全一致 */
type ImportFixture = Omit<ImportResponse, 'validation' | 'soft_metrics'>;

const meta = metaJson as unknown as Meta;

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

export function mockImport(file: File, visionModel?: string | null): ImportResponse {
  const fixture = resolveImportFixture(file.name);
  const body = JSON.parse(JSON.stringify(fixture)) as ImportFixture;
  const { validation, soft_metrics } = mockValidate(meta, normalizeImportedSlots(meta, body.slots));
  return {
    ...body,
    // 视觉模型由用户在选择器里指定，mock 也要如实回显，否则选择器看着像没生效
    model_used: body.extractor === 'vision_llm' ? (visionModel ?? body.model_used) : body.model_used,
    validation,
    soft_metrics,
  };
}

/** 演示站没有后端提供 /api/import/template，用同结构的常量兜住模板下载入口 */
export const MOCK_TEMPLATE_CSV = [
  '# 智能排班助手 · 导入模板（长表）。删掉本注释行也能正常导入',
  '# 员工列可用空格 / 逗号 / 顿号分隔；工号写 E01 或 1 都能识别，超出 E01–E20 会被列为未匹配',
  '日期,班次,员工',
  '周一,早班,E01 E05 E06 E08 E11',
  '周一,晚班,E02 E07 E09 E12 E16',
  '周二,早班,E03 E05 E06 E08 E17',
  '周二,晚班,E04 E07 E09 E10 E18',
  '周三,早班,E01 E06 E08 E11 E17',
  '周三,晚班,E02 E07 E09 E12 E16',
  '周四,早班,E03 E05 E11 E15 E17',
  '周四,晚班,E04 E10 E12 E14 E18',
  '周五,早班,E03 E05 E08 E15 E17',
  '周五,晚班,E02 E09 E14 E16 E19',
  '周六,早班,E01 E06 E08 E13 E15 E17',
  '周六,晚班,E04 E10 E14 E16 E18 E19',
  '周日,早班,E01 E05 E08 E13 E15 E11',
  '周日,晚班,E02 E10 E12 E14 E16 E19',
].join('\n');

/** 演示样例文件：静态站上用户手边没有排班表，直接给三个可点的场景 */
export const MOCK_SAMPLE_FILES: Array<{ label: string; hint: string; build: () => File }> = [
  {
    label: '干净 CSV',
    hint: '14 格全解析，体检全部通过',
    build: () => new File([MOCK_TEMPLATE_CSV], '门店排班表-下周.csv', { type: 'text/csv' }),
  },
  {
    label: '有违规的 CSV',
    hint: '解析成功但体检查出 4 条硬规则违规',
    build: () => new File([MOCK_TEMPLATE_CSV], '门店排班表-违规样例.csv', { type: 'text/csv' }),
  },
  {
    label: '排班表图片',
    hint: '视觉识别约 10 秒，含缺格与未匹配项',
    build: () => new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], '排班表拍照.png', { type: 'image/png' }),
  },
];
