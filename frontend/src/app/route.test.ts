import { describe, expect, it } from 'vitest'
import { hashForSkill, skillScreenFromHash, viewFromHash, type SkillScreen } from './route'

describe('the address', () => {
  it('names the page, and anything else is Trading', () => {
    expect(viewFromHash('#/skill')).toBe('skill')
    expect(viewFromHash('#/skill/report/abc')).toBe('skill')
    expect(viewFromHash('#/plan')).toBe('plan')
    expect(viewFromHash('')).toBe('trading')
    expect(viewFromHash('#/nowhere')).toBe('trading')
    expect(viewFromHash('#/confirm/s1?e=123&s=abc')).toBe('trading') // a signal's confirm link
  })

  it("names Skill Test's screen and reads back the same", () => {
    const screens: SkillScreen[] = [
      { name: 'lobby' },
      { name: 'setup' },
      { name: 'arena', id: 'a1' },
      { name: 'report', id: 'r1' },
      { name: 'history' },
    ]
    for (const screen of screens) {
      expect(skillScreenFromHash(hashForSkill(screen))).toEqual(screen)
    }
    expect(skillScreenFromHash('#/skill/day')).toEqual({ name: 'lobby' }) // no id: the lobby
  })
})
