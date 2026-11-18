import { describe, expect, it } from 'vitest';
import { delayStatus } from './logic';
describe('delay thresholds', () => {
  it('distinguishes on-time, minor, and above-threshold delays', () => {
    expect(delayStatus(0, 10)).toBe('on-time');
    expect(delayStatus(10, 10)).toBe('minor-delay');
    expect(delayStatus(11, 10)).toBe('delayed');
  });
});
