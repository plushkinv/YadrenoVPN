import { createRoot } from 'react-dom/client';
import { App } from './App';
import { loadEnvironment } from './runtime/environment';
import './styles.css';
import './app.css';

loadEnvironment().then(environment => createRoot(document.getElementById('root')!).render(<App environment={environment} />));
