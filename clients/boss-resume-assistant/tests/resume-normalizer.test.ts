import assert from 'node:assert/strict'
import test from 'node:test'
import { normalize, type ListSummarySource } from '../src/main/normalize/ResumeNormalizer.js'

const fps = { candidate: 'cand-fp', job: 'job-fp' }

test('OCR 与列表姓名一致 → 用该姓名，无冲突', () => {
  const r = normalize(
    { baseInfo: { name: '张三' } },
    { fields: { name: '张三', age: '28', degree: '本科', city: '北京' } },
    fps,
  )
  assert.equal(r.summary.baseInfo.name, '张三')
  assert.equal(r.summary.completeness, 'COMPLETE_SUMMARY')
  assert.equal(r.reviewIssues.length, 0)
})

test('OCR 与列表姓名冲突 → 进 reviewIssues，不静默覆盖', () => {
  const r = normalize(
    { baseInfo: { name: '李四' } },
    { fields: { name: '张三' } },
    fps,
  )
  assert.equal(r.reviewIssues.length, 1)
  assert.equal(r.reviewIssues[0]!.field, 'baseInfo.name')
  assert.equal(r.reviewIssues[0]!.listValue, '张三')
  assert.equal(r.reviewIssues[0]!.ocrValue, '李四')
  // 冲突时降级为 PARTIAL_SUMMARY
  assert.equal(r.summary.completeness, 'PARTIAL_SUMMARY')
})

test('列表有 recentCompany、OCR 无工作经历 → 列表优先填充', () => {
  const r = normalize(
    { baseInfo: { name: '张三' } },
    { fields: { name: '张三', recentCompany: '阿里', recentPosition: '前端' } },
    fps,
  )
  assert.equal(r.summary.workExperiences.length, 1)
  assert.equal(r.summary.workExperiences[0]!.company, '阿里')
  assert.equal(r.summary.workExperiences[0]!.position, '前端')
})

test('列表与 OCR 工作经历公司冲突 → 进 reviewIssues', () => {
  const r = normalize(
    {
      baseInfo: { name: '张三' },
      workExperiences: [{ company: '腾讯', position: '前端' }],
    },
    { fields: { name: '张三', recentCompany: '阿里' } },
    fps,
  )
  assert.ok(r.reviewIssues.some((i) => i.field === 'workExperiences[0].company'))
})

test('Markdown 含姓名、元信息、工作经历章节', () => {
  const r = normalize(
    {
      baseInfo: { name: '张三' },
      workExperiences: [{ company: '腾讯', position: '前端', start: '2020', end: '2023' }],
    },
    { fields: { name: '张三', age: '28', degree: '本科', workYears: '5', city: '北京' } },
    fps,
  )
  assert.match(r.markdown, /# 张三/)
  assert.match(r.markdown, /28 \/ 本科 \/ 5年 \/ 北京/)
  assert.match(r.markdown, /## 工作经历/)
  assert.match(r.markdown, /\*\*腾讯\*\* 前端 2020 - 2023/)
})

test('summary 带 source=OCR_NORMALIZED 与指纹', () => {
  const r = normalize({ baseInfo: { name: '张三' } }, { fields: { name: '张三' } }, fps)
  assert.equal(r.summary.source, 'OCR_NORMALIZED')
  assert.equal(r.summary.candidateFingerprint, 'cand-fp')
  assert.equal(r.summary.jobFingerprint, 'job-fp')
})
