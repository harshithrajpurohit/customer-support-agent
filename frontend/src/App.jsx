import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import ChatWidget from './components/ChatWidget';
import AdminPortal from './components/AdminPortal';

export default function App() {
  // Determine initial view from pathname or hash
  const getInitialView = () => {
    const path = window.location.pathname.toLowerCase();
    const hash = window.location.hash.toLowerCase();
    if (path.startsWith('/admin') || hash.includes('admin')) {
      return 'admin';
    }
    return 'chat';
  };

  const [currentView, setCurrentView] = useState(getInitialView);
  const [toasts, setToasts] = useState([]);

  // Sync state with browser URL popstate
  useEffect(() => {
    const handlePopState = () => {
      setCurrentView(getInitialView());
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Change view and push state to history
  const handleViewChange = (view) => {
    setCurrentView(view);
    const targetPath = view === 'admin' ? '/admin' : '/';
    if (window.location.pathname !== targetPath) {
      window.history.pushState({ view }, '', targetPath);
    }
  };

  // Toast Notification helper
  const showToast = (message, type = 'error') => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, type }]);

    // Auto dismiss after 5 seconds
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  };

  const removeToast = (id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  return (
    <div className="app-container">
      <Header currentView={currentView} onViewChange={handleViewChange} />

      {/* Global Toast Notifications */}
      {toasts.length > 0 && (
        <div className="toast-container">
          {toasts.map((toast) => (
            <div key={toast.id} className={`toast toast-${toast.type}`}>
              <span style={{ fontSize: '1.1rem' }}>
                {toast.type === 'error' ? '⚠️' : '✅'}
              </span>
              <div className="toast-content">
                <div className="toast-title">
                  {toast.type === 'error' ? 'Notice' : 'Success'}
                </div>
                <div>{toast.message}</div>
              </div>
              <button
                type="button"
                className="toast-close"
                onClick={() => removeToast(toast.id)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Main View Area */}
      <main className="main-content">
        {currentView === 'chat' ? (
          <ChatWidget onShowToast={showToast} />
        ) : (
          <AdminPortal onShowToast={showToast} />
        )}
      </main>
    </div>
  );
}
