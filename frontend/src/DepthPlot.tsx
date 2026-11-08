import React, {useEffect, useRef} from 'react';
import Plotly from 'plotly.js-dist-min';
import type {Data} from 'plotly.js';

export type DepthSeries = {name: string; x: (number | null)[]; y: number[]; color: string; dash?: 'dash' | 'dot'; markers?: boolean};

// Values against depth, depth increasing downwards. The series are plotted
// as given; the component computes nothing.
export function DepthPlot({series, xLabel, yLabel, label, height = 300}: {series: DepthSeries[]; xLabel: string; yLabel: string; label: string; height?: number}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current!;
    const traces: Data[] = series.map(s => ({name: s.name, x: s.x, y: s.y, type: 'scatter', mode: s.markers ? 'lines+markers' : 'lines', line: {color: s.color, width: 2, dash: s.dash}, marker: {size: 5}}));
    void Plotly.react(node, traces, {
      font: {family: 'Inter, Segoe UI, sans-serif', color: '#657585', size: 11}, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
      margin: {l: 62, r: 16, t: 28, b: 44}, height, autosize: true, showlegend: series.length > 1, legend: {orientation: 'h', x: 0, y: 1.16, font: {size: 10}},
      xaxis: {title: {text: xLabel}, gridcolor: '#edf0f0', zerolinecolor: '#c7d2d4'},
      yaxis: {title: {text: yLabel}, autorange: 'reversed', gridcolor: '#edf0f0', zerolinecolor: '#c7d2d4'},
    }, {displayModeBar: false, responsive: true});
    const observer = new ResizeObserver(() => Plotly.Plots.resize(node));
    observer.observe(node);
    return () => observer.disconnect();
  }, [series, xLabel, yLabel, height]);
  useEffect(() => () => { if (ref.current) Plotly.purge(ref.current); }, []);
  return <div className="depth-plot" ref={ref} aria-label={label} />;
}
