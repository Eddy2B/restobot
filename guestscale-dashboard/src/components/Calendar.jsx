import { useState } from 'react';
import { useStore } from '../utils/store';
import { fmtDate, parseDateLocal, MONTH_NAMES, MONTH_SHORT, DOW_NAMES } from '../utils/dateUtils';

export default function Calendar() {
  const { state, dispatch } = useStore();
  const { selectedDate, bookings } = state;
  const [pickerMode, setPickerMode] = useState(null);

  const sel = parseDateLocal(selectedDate);
  const today = fmtDate(new Date());
  const year = sel.getFullYear();
  const month = sel.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const startIdx = (firstDay + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrev = new Date(year, month, 0).getDate();

  function setDate(ds) {
    dispatch({ type: 'SET_DATE', payload: ds });
  }

  function shiftMonth(n) {
    const d = parseDateLocal(selectedDate);
    d.setMonth(d.getMonth() + n);
    setDate(fmtDate(d));
  }

  function countBookings(ds) {
    return bookings.filter(b => (b.date || '').startsWith(ds)).length;
  }

  // Build prev month cells
  const prevCells = [];
  for (let i = startIdx - 1; i >= 0; i--) {
    const day = daysInPrev - i;
    const pm = month === 0 ? 11 : month - 1;
    const py = month === 0 ? year - 1 : year;
    const ds = fmtDate(new Date(py, pm, day));
    prevCells.push({ day, ds, other: true });
  }

  const currentCells = [];
  for (let d = 1; d <= daysInMonth; d++) {
    const ds = fmtDate(new Date(year, month, d));
    currentCells.push({ day: d, ds, isToday: ds === today, isSel: ds === selectedDate });
  }

  const total = startIdx + daysInMonth;
  const remaining = (7 - total % 7) % 7;
  const nextCells = [];
  for (let i = 1; i <= remaining; i++) {
    const nm = month === 11 ? 0 : month + 1;
    const ny = month === 11 ? year + 1 : year;
    const ds = fmtDate(new Date(ny, nm, i));
    nextCells.push({ day: i, ds, other: true });
  }

  return (
    <div className="cal-wrap">
      <div className="cal-header">
        <div className="cal-nav">
          <div className="cal-arrow" onClick={() => shiftMonth(-1)}>&#8249;</div>
          <div className="cal-title" onClick={() => {
            if (pickerMode === 'month') setPickerMode('year');
            else if (pickerMode) setPickerMode(null);
            else setPickerMode('month');
          }}>{MONTH_NAMES[month]} {year}</div>
          <div className="cal-arrow" onClick={() => shiftMonth(1)}>&#8250;</div>
        </div>
        <div className="cal-today-btn" onClick={() => setDate(fmtDate(new Date()))}>Aujourd&apos;hui</div>
      </div>

      {pickerMode && (
        <div className="cal-picker show">
          <div className="cal-picker-grid">
            {pickerMode === 'month' ? MONTH_SHORT.map((m, i) => (
              <div key={i} className={'cal-picker-item' + (i === sel.getMonth() ? ' sel' : '')}
                onClick={() => { const d = parseDateLocal(selectedDate); d.setMonth(i); setDate(fmtDate(d)); setPickerMode(null); }}>
                {m}
              </div>
            )) : Array.from({ length: 9 }, (_, i) => year - 4 + i).map(y => (
              <div key={y} className={'cal-picker-item' + (y === year ? ' sel' : '')}
                onClick={() => { const d = parseDateLocal(selectedDate); d.setFullYear(y); setDate(fmtDate(d)); setPickerMode(null); }}>
                {y}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="cal-grid">
        {DOW_NAMES.map((d, i) => <div key={i} className="cal-dow">{d}</div>)}
        {prevCells.map(c => (
          <div key={'p' + c.day} className="cal-cell other" onClick={() => setDate(c.ds)}>
            <span className="cal-num">{c.day}</span>
            <span className={'cal-dot' + (countBookings(c.ds) > 0 ? ' has' : '')} />
          </div>
        ))}
        {currentCells.map(c => (
          <div key={c.day} className={'cal-cell' + (c.isSel ? ' sel' : '') + (c.isToday && !c.isSel ? ' today' : '')}
            onClick={() => setDate(c.ds)}>
            <span className="cal-num">{c.day}</span>
            <span className={'cal-dot' + (countBookings(c.ds) > 0 ? ' has' : '')} />
          </div>
        ))}
        {nextCells.map(c => (
          <div key={'n' + c.day} className="cal-cell other" onClick={() => setDate(c.ds)}>
            <span className="cal-num">{c.day}</span>
            <span className={'cal-dot' + (countBookings(c.ds) > 0 ? ' has' : '')} />
          </div>
        ))}
      </div>
    </div>
  );
}
