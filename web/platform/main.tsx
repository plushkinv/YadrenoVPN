import { createRoot } from 'react-dom/client';
import { Runtime } from './Runtime';
import { loadEnvironment } from './runtime/environment';
import { ApplicationBoundary } from './ApplicationBoundary';
import './system.css';

loadEnvironment().then(environment => createRoot(document.getElementById('platform')!).render(
  <ApplicationBoundary reload={() => location.reload()}><Runtime environment={environment} /></ApplicationBoundary>));
