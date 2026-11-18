export function delayStatus(delay: number, threshold: number): 'on-time' | 'minor-delay' | 'delayed' {
  return delay > threshold ? 'delayed' : delay > 0 ? 'minor-delay' : 'on-time';
}
