import React from 'react';
import { createRoot } from 'react-dom/client';
import DailyAttendanceApp from './DailyAttendanceApp';

const mountEl = document.getElementById('daily-attendance-react-root');
if (mountEl) {
  let initialConfig = {};
  try {
    initialConfig = JSON.parse(mountEl.dataset.config || '{}');
  } catch (e) {
    console.error('Failed to parse attendance initial config:', e);
  }
  createRoot(mountEl).render(<DailyAttendanceApp initialConfig={initialConfig} />);
}
