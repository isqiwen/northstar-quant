// Display-only indicators over the fixed range, using current and preceding bars.
// Missing prices reset warmup; these values never enter strategy or account inputs.
export function movingAverage(values: (number | null)[], period: number) {
  const result: (number | null)[] = [];
  let sum = 0;
  let valid = 0;
  for (let i = 0; i < values.length; i++) {
    const value = values[i];
    if (value == null) {
      sum = 0;
      valid = 0;
    } else {
      sum += value;
      valid++;
      if (valid > period) sum -= values[i - period]!;
    }
    result.push(valid >= period ? sum / period : null);
  }
  return result;
}
export function macd(values: (number | null)[]) {
  let fast: number | null = null;
  let slow: number | null = null;
  let signal: number | null = null;
  const dif: (number | null)[] = [],
    dea: (number | null)[] = [],
    histogram: (number | null)[] = [];
  for (const value of values) {
    if (value == null) {
      fast = slow = signal = null;
      dif.push(null);
      dea.push(null);
      histogram.push(null);
      continue;
    }
    fast = fast == null ? value : fast + ((value - fast) * 2) / 13;
    slow = slow == null ? value : slow + ((value - slow) * 2) / 27;
    const difference = fast - slow;
    signal =
      signal == null ? difference : signal + ((difference - signal) * 2) / 10;
    dif.push(difference);
    dea.push(signal);
    histogram.push(2 * (difference - signal));
  }
  return { dif, dea, histogram };
}
