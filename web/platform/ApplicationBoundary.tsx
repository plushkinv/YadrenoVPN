import { Component, type ReactNode } from 'react';

export class ApplicationBoundary extends Component<{ children: ReactNode; reload: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div role="alert" style={{ padding: 24, fontFamily: 'system-ui', background: '#fff', color: '#162a23' }}>
      <h1>Не удалось показать интерфейс</h1><p>Попробуйте открыть приложение повторно.</p>
      <button type="button" onClick={this.props.reload}>Повторить</button>
    </div>;
  }
}
