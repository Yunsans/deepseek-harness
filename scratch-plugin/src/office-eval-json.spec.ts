import { describe, expect, it } from 'vitest'
import { parseJsonObject, repairJsonText } from './office-eval-pipeline.ts'

describe('repairJsonText', () => {
  it('inserts missing commas between array elements', () => {
    const raw = '{"questions":[{"id":"Q1"} {"id":"Q2"}]}'
    expect(repairJsonText(raw)).toBe('{"questions":[{"id":"Q1"},{"id":"Q2"}]}')
  })

  it('inserts missing commas between object properties', () => {
    const raw = '{"a":1 "b":2}'
    expect(repairJsonText(raw)).toBe('{"a":1,"b":2}')
  })

  it('inserts missing commas between string array elements', () => {
    const raw = '{"strengths":["一" "二"]}'
    expect(repairJsonText(raw)).toBe('{"strengths":["一","二"]}')
  })

  it('inserts a comma before a following number element', () => {
    const raw = '{"a":[1 2]}'
    expect(repairJsonText(raw)).toBe('{"a":[1,2]}')
  })

  it('drops trailing commas', () => {
    expect(repairJsonText('{"a":[1,2,]}')).toBe('{"a":[1,2]}')
    expect(repairJsonText('{"a":1, }')).toBe('{"a":1 }')
  })

  it('escapes raw newlines inside strings', () => {
    const raw = '{"oneLiner":"第一行\n第二行"}'
    const repaired = repairJsonText(raw)
    expect(repaired).not.toBeNull()
    expect(JSON.parse(repaired as string)).toEqual({ oneLiner: '第一行\n第二行' })
  })

  it('converts single-quoted strings to double-quoted strings', () => {
    expect(repairJsonText("{'a': 'b'}")).toBe('{"a": "b"}')
  })

  it('escapes double quotes inside single-quoted strings', () => {
    const repaired = repairJsonText("{'a': '他说\"你好\"'}")
    expect(repaired).toBe('{"a": "他说\\"你好\\""}')
    expect(JSON.parse(repaired as string)).toEqual({ a: '他说"你好"' })
  })

  it('returns null when the input already parses (nothing changed)', () => {
    expect(repairJsonText('{"a":1}')).toBeNull()
  })

  it('returns null when no repair can make the input parse', () => {
    expect(repairJsonText('{"a": }')).toBeNull()
    expect(repairJsonText('not json at all')).toBeNull()
  })
})

describe('parseJsonObject', () => {
  it('parses plain JSON objects', () => {
    expect(parseJsonObject('{"a":1}')).toEqual({ a: 1 })
  })

  it('parses fenced JSON blocks', () => {
    expect(parseJsonObject('```json\n{"a":1}\n```')).toEqual({ a: 1 })
  })

  it('parses prose-wrapped JSON by slicing the first object', () => {
    expect(parseJsonObject('以下是结果：\n{"a":1}\n完毕')).toEqual({ a: 1 })
  })

  it('repairs missing commas before returning', () => {
    const value = parseJsonObject('{"questions":[{"id":"Q1"} {"id":"Q2"}]}')
    expect(value).toEqual({ questions: [{ id: 'Q1' }, { id: 'Q2' }] })
  })

  it('throws a diagnostic error with an excerpt for unfixable JSON', () => {
    expect(() => parseJsonObject('{"a": }')).toThrow(/评测模型输出不是合法 JSON/)
    expect(() => parseJsonObject('没有 JSON')).toThrow(/评测模型输出中没有 JSON 对象/)
  })
})
