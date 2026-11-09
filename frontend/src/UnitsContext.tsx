import React, {createContext, useContext, useEffect, useLayoutEffect, useRef, useState} from 'react';
import {api} from './apiClient';
import {fromSI, inputValue, symbol, toSI, format, type Quantity, type UnitSystem} from './units';

type Units = {system: UnitSystem; setSystem: (system: UnitSystem) => void; error: string};
const UnitsContext = createContext<Units>({system: 'SI', setSystem: () => undefined, error: ''});

// The unit choice is an application setting stored by the local service.
// It never changes a scenario or its hash.
export function UnitsProvider({children}: {children: React.ReactNode}) {
  const [system, setLocal] = useState<UnitSystem>('SI');
  const [error, setError] = useState('');
  useEffect(() => {
    void api<{units: UnitSystem}>('/settings').then(value => setLocal(value.units)).catch(e => setError((e as Error).message));
  }, []);
  const setSystem = (next: UnitSystem) => {
    setLocal(next);
    void api<{units: UnitSystem}>('/settings', {method: 'PUT', body: JSON.stringify({units: next})}).then(() => setError('')).catch(e => setError((e as Error).message));
  };
  return <UnitsContext.Provider value={{system, setSystem, error}}>{children}</UnitsContext.Provider>;
}

export const useUnits = () => useContext(UnitsContext);

export function UnitToggle() {
  const {system, setSystem, error} = useUnits();
  return <div className="unit-toggle" role="group" aria-label="Units">
    <span>Units</span>
    {(['SI', 'oilfield'] as const).map(value => <button key={value} aria-pressed={system === value} onClick={() => setSystem(value)}>{value === 'SI' ? 'SI' : 'Oilfield'}</button>)}
    {error && <span className="unit-error" title={error}>Not saved</span>}
  </div>;
}

// A number box that shows a value in the chosen units and returns SI.
// An unedited box never writes back, so changing units leaves the scenario
// and its hash unchanged.
export function UnitInput({label, value, quantity, change, invalid, onInvalid, min, ariaLabel}: {label: string; value: number | null | undefined; quantity: Quantity; change: (si: number) => void; invalid?: boolean; onInvalid?: (invalid: boolean) => void; min?: number; ariaLabel?: string}) {
  const {system} = useUnits();
  const shown = value == null ? '' : String(inputValue(value, quantity, system));
  const [text, setText] = useState(shown);
  const focused = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null), feedbackStarted = useRef(0);
  useLayoutEffect(() => { if (feedbackStarted.current && inputRef.current) inputRef.current.dataset.feedbackMs = (performance.now() - feedbackStarted.current).toFixed(3); }, [text]);
  useEffect(() => { if (!focused.current) setText(shown); }, [shown, invalid]);
  return <input ref={inputRef} type="number" step="any" aria-label={ariaLabel || `${label} (${symbol(quantity, system)})`} value={text} min={min} aria-invalid={invalid || undefined}
    onFocus={() => { focused.current = true; }}
    onBlur={() => { focused.current = false; if (text !== '' && Number.isFinite(+text)) setText(shown); }}
    onChange={e => {
      feedbackStarted.current = performance.now();
      const nextText = e.currentTarget.value;
      setText(nextText);
      const next = e.currentTarget.valueAsNumber;
      onInvalid?.(!Number.isFinite(next));
      if (Number.isFinite(next)) change(toSI(next, quantity, system));
    }} />;
}

export function UnitField({label, value, quantity, change, hint, invalid, onInvalid, min}: {label: string; value: number | null | undefined; quantity: Quantity; change: (si: number) => void; hint?: React.ReactNode; invalid?: boolean; onInvalid?: (invalid: boolean) => void; min?: number}) {
  const {system} = useUnits();
  return <label className="field">
    <span className="field-label">{label}<span className="field-unit">{symbol(quantity, system)}</span></span>
    <div className="number-row"><UnitInput label={label} value={value} quantity={quantity} change={change} invalid={invalid} onInvalid={onInvalid} min={min} /><span>{symbol(quantity, system)}</span></div>
    {hint && <small>{hint}</small>}
  </label>;
}

export function useFormat() {
  const {system} = useUnits();
  return {
    system,
    format: (value: number | null | undefined, quantity: Quantity, places?: number) => format(value, quantity, system, places),
    value: (value: number, quantity: Quantity) => fromSI(value, quantity, system),
    symbol: (quantity: Quantity) => symbol(quantity, system),
  };
}

// A number box without a unit conversion. Like UnitInput it keeps the typed
// text while editing, so the box can be cleared and retyped; only a finite
// number is passed on.
export function NumberInput({value, change, ariaLabel, step = 'any', integer = false}: {value: number | null | undefined; change: (value: number) => void; ariaLabel: string; step?: number | 'any'; integer?: boolean}) {
  const shown = value == null ? '' : String(value);
  const [text, setText] = useState(shown);
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setText(shown); }, [shown]);
  return <input type="number" step={step} aria-label={ariaLabel} value={text}
    onFocus={() => { focused.current = true; }}
    onBlur={() => { focused.current = false; setText(shown); }}
    onChange={e => {
      setText(e.currentTarget.value);
      const next = e.currentTarget.valueAsNumber;
      if (Number.isFinite(next)) change(integer ? Math.round(next) : next);
    }} />;
}

