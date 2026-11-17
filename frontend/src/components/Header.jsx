import React, { useState, useEffect } from 'react';
import { checkHealth } from '../api';

export default function Header({ currentView, onViewChange }) {
  const [health, setHealth] = useState({ status: 'checking', db: false });

  useEffect(() => {
    let mounted = true;

    async function fetchHealth() {
      try {
        const data = await checkHealth();
        if (mounted) {
          setHealth(data);
        }
      } catch {
        if (mounted) {
          setHealth({ status: 'offline', db: false });
        }
      }
    }

    fetchHealth();
    const interval = setInterval(fetchHealth, 10000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const healthClass =
    health.status === 'ok'
      ? 'online'
      : health.status === 'degraded'
      ? 'degraded'
      : 'offline';

  const healthLabel =
    health.status === 'ok'
      ? 'API: Online'
      : health.status === 'degraded'
      ? 'API: Degraded'
      : health.status === 'checking'
      ? 'API: Checking...'
      : 'API: Offline';

  return (
    <header className="app-header">
      <div className="header-content">
        <a
          href="/"
          className="brand"
          onClick={(e) => {
            e.preventDefault();
            onViewChange('chat');
          }}
        >
          <div className="brand-icon">🤖</div>
          <span>SupportDesk AI</span>
        </a>

        <nav className="nav-links">
          <button
            type="button"
            className={`nav-button ${currentView === 'chat' ? 'active' : ''}`}
            onClick={() => onViewChange('chat')}
          >
            <span>💬</span>
            <span>Customer Support Chat</span>
          </button>
          <button
            type="button"
            className={`nav-button ${currentView === 'admin' ? 'active' : ''}`}
            onClick={() => onViewChange('admin')}
          >
            <span>🛡️</span>
            <span>Admin Portal</span>
          </button>
        </nav>

        <div className="header-meta">
          <div className="health-badge" title={`API health: ${health.status}`}>
            <span className={`health-dot ${healthClass}`} />
            <span>{healthLabel}</span>
          </div>
        </div>
      </div>
    </header>
  );
}
