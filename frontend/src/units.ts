// Display units. The table and its factors come from the application's single
// units module, written to contracts/units.json by the contracts command.
// Scenarios, archives and the model always hold SI values.
import table from '../../contracts/units.json' with {type: 'json'};

export type UnitSystem = 'SI' | 'oilfield';
export type Quantity = keyof typeof table.quantities;
type Unit = {symbol: string; factor: number; offset: number; decimals: number};

export const UNIT_TABLE = table;
export const SYSTEMS = table.systems as UnitSystem[];
const FIGURES = table.entry_significant_figures;

function unit(quantity: Quantity, system: UnitSystem): Unit {
  const entry = (table.quantities[quantity].units as Record<string, Unit>)[system];
  if (!entry) throw new Error(`Unknown unit system ${system}`);
  return entry;
}

export function roundSignificant(value: number, figures = FIGURES): number {
  if (value === 0 || !Number.isFinite(value)) return value;
  return Number(value.toExponential(figures - 1));
}

export const symbol = (quantity: Quantity, system: UnitSystem) => unit(quantity, system).symbol;

export function fromSI(value: number, quantity: Quantity, system: UnitSystem): number {
  const {factor, offset} = unit(quantity, system);
  return value / factor - offset;
}

// Entered display values become SI values to 12 significant figures, so the
// same physical entry in either system gives the same scenario.
export function toSI(value: number, quantity: Quantity, system: UnitSystem): number {
  const {factor, offset} = unit(quantity, system);
  if (factor === 1 && offset === 0) return value;
  return roundSignificant((value + offset) * factor);
}

// Value placed in an input box. Ten significant figures avoids showing
// conversion noise; an unedited box never writes back to the scenario.
export function inputValue(value: number, quantity: Quantity, system: UnitSystem): number {
  const shown = fromSI(value, quantity, system);
  return Number.isFinite(shown) ? Number(shown.toPrecision(10)) : shown;
}

export function decimals(quantity: Quantity, system: UnitSystem): number {
  return unit(quantity, system).decimals;
}

// Number text without the minus sign of a value that rounds to zero, such as "-0.0".
export function unsignedZero(text: string): string {
  return text.startsWith('-') && !/[1-9]/.test(text) ? text.slice(1) : text;
}

export function number(value: number | null | undefined, quantity: Quantity, system: UnitSystem, places?: number): string {
  if (value == null || !Number.isFinite(value)) return 'Unavailable';
  const digits = places ?? decimals(quantity, system);
  return unsignedZero(fromSI(value, quantity, system).toLocaleString('en-GB', {maximumFractionDigits: digits, minimumFractionDigits: digits}));
}

export function format(value: number | null | undefined, quantity: Quantity, system: UnitSystem, places?: number): string {
  const text = number(value, quantity, system, places);
  return text === 'Unavailable' ? text : `${text} ${symbol(quantity, system)}`;
}

// A change with its sign: "+" only where the value does not round to zero.
export function signed(value: number, quantity: Quantity, system: UnitSystem, places?: number): string {
  const text = format(value, quantity, system, places);
  return value > 0 && /[1-9]/.test(number(value, quantity, system, places)) ? `+${text}` : text;
}

// Converts a series for plotting or tables.
export const series = (values: number[], quantity: Quantity, system: UnitSystem) => values.map(v => fromSI(v, quantity, system));
