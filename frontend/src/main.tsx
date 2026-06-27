import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import ErrorBoundary from './components/common/error-boundary';
import { initTaxRates } from './lib/constants';
import './index.css';

// Render immediately. Blocking the first paint on initTaxRates() left the
// screen blank whenever that request was slow or hung. TAX_RATES ships with
// safe built-in defaults and initTaxRates() swallows its own errors, so it is
// fine to refresh the rates in the background after mount.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);

void initTaxRates();
