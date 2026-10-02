import { Component, type ReactNode } from 'react';

/** Display runtime failures without exposing source paths or account data. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="notice notice--error" role="alert"><h1>Не удалось показать интерфейс</h1>
      <p>Перезагрузите страницу. Если ошибка повторяется, сообщите администратору.</p>
      <button type="button" className="button button--secondary" onClick={() => location.reload()}>Перезагрузить</button></div>;
  }
}
