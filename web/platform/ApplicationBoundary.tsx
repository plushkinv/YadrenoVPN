import { text as _tr } from "./i18n/runtime";
import { Component, type ReactNode } from 'react';

export class ApplicationBoundary extends Component<{ children: ReactNode; reload: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div role="alert" style={{ padding: 24, fontFamily: 'system-ui', background: '#fff', color: '#162a23' }}>
      <h1>{_tr("ApplicationBoundary.1")}</h1><p>{_tr("ApplicationBoundary.2")}</p>
      <button type="button" onClick={this.props.reload}>{_tr("ApplicationBoundary.3")}</button>
    </div>;
  }
}
