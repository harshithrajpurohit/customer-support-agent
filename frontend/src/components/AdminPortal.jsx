import React, { useState, useEffect } from 'react';
import {
  getAdminRefunds,
  decideAdminRefund,
  getAdminHandoffs,
  getAdminHandoffDetail,
  resolveAdminHandoff,
} from '../api';

export default function AdminPortal({ onShowToast }) {
  // Admin Token State (stored strictly in sessionStorage)
  const [adminToken, setAdminToken] = useState(() => {
    return sessionStorage.getItem('support_admin_token') || '';
  });
  const [tokenInput, setTokenInput] = useState('');
  const [activeTab, setActiveTab] = useState('refunds'); // 'refunds' | 'handoffs'

  // Refund approvals state
  const [refunds, setRefunds] = useState([]);
  const [refundStatusFilter, setRefundStatusFilter] = useState('pending');
  const [isLoadingRefunds, setIsLoadingRefunds] = useState(false);
  const [refundNotes, setRefundNotes] = useState({}); // { [refundId]: string }
  const [actionInProgress, setActionInProgress] = useState({}); // { [refundId]: 'approve' | 'reject' | null }

  // Handoff inbox state
  const [handoffs, setHandoffs] = useState([]);
  const [handoffStatusFilter, setHandoffStatusFilter] = useState('open');
  const [isLoadingHandoffs, setIsLoadingHandoffs] = useState(false);
  const [resolvingHandoffId, setResolvingHandoffId] = useState(null);

  // Transcript Modal State
  const [selectedHandoffId, setSelectedHandoffId] = useState(null);
  const [handoffDetail, setHandoffDetail] = useState(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  // Save token to sessionStorage
  const handleSaveToken = (e) => {
    e.preventDefault();
    const cleanToken = tokenInput.trim();
    if (!cleanToken) {
      onShowToast('Please enter an admin token', 'error');
      return;
    }
    sessionStorage.setItem('support_admin_token', cleanToken);
    setAdminToken(cleanToken);
    setTokenInput('');
  };

  const handleSignOut = () => {
    sessionStorage.removeItem('support_admin_token');
    setAdminToken('');
    setRefunds([]);
    setHandoffs([]);
  };

  // Fetch Refunds
  const fetchRefunds = async (status = refundStatusFilter) => {
    if (!adminToken) return;
    setIsLoadingRefunds(true);
    try {
      const data = await getAdminRefunds(adminToken, status);
      setRefunds(data || []);
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        onShowToast('Admin authentication failed. Please re-enter your admin token.', 'error');
        handleSignOut();
      } else {
        onShowToast(`Failed to load refunds: ${err.message}`, 'error');
      }
    } finally {
      setIsLoadingRefunds(false);
    }
  };

  // Fetch Handoffs
  const fetchHandoffs = async (status = handoffStatusFilter) => {
    if (!adminToken) return;
    setIsLoadingHandoffs(true);
    try {
      const data = await getAdminHandoffs(adminToken, status);
      setHandoffs(data || []);
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        onShowToast('Admin authentication failed. Please re-enter your admin token.', 'error');
        handleSignOut();
      } else {
        onShowToast(`Failed to load handoffs: ${err.message}`, 'error');
      }
    } finally {
      setIsLoadingHandoffs(false);
    }
  };

  // Fetch data on token or tab change
  useEffect(() => {
    if (!adminToken) return;
    if (activeTab === 'refunds') {
      fetchRefunds(refundStatusFilter);
    } else {
      fetchHandoffs(handoffStatusFilter);
    }
  }, [adminToken, activeTab, refundStatusFilter, handoffStatusFilter]);

  // Handle Refund Decision
  const handleRefundDecision = async (refundId, decision) => {
    const note = refundNotes[refundId] || '';
    setActionInProgress((prev) => ({ ...prev, [refundId]: decision }));

    try {
      await decideAdminRefund(adminToken, refundId, decision, note);
      onShowToast(
        `Refund #${refundId} successfully ${decision === 'approve' ? 'approved' : 'rejected'}!`,
        'success'
      );
      // Clear note
      setRefundNotes((prev) => {
        const next = { ...prev };
        delete next[refundId];
        return next;
      });
      // Refresh list
      fetchRefunds();
    } catch (err) {
      onShowToast(`Failed to ${decision} refund #${refundId}: ${err.message}`, 'error');
    } finally {
      setActionInProgress((prev) => ({ ...prev, [refundId]: null }));
    }
  };

  // View Handoff Details
  const handleOpenTranscript = async (handoffId) => {
    setSelectedHandoffId(handoffId);
    setHandoffDetail(null);
    setIsLoadingDetail(true);

    try {
      const detail = await getAdminHandoffDetail(adminToken, handoffId);
      setHandoffDetail(detail);
    } catch (err) {
      onShowToast(`Failed to load transcript for handoff #${handoffId}: ${err.message}`, 'error');
      setSelectedHandoffId(null);
    } finally {
      setIsLoadingDetail(false);
    }
  };

  // Resolve Handoff
  const handleResolveHandoff = async (handoffId) => {
    setResolvingHandoffId(handoffId);
    try {
      await resolveAdminHandoff(adminToken, handoffId);
      onShowToast(`Handoff #${handoffId} marked as resolved!`, 'success');
      if (selectedHandoffId === handoffId) {
        setSelectedHandoffId(null);
        setHandoffDetail(null);
      }
      fetchHandoffs();
    } catch (err) {
      onShowToast(`Failed to resolve handoff #${handoffId}: ${err.message}`, 'error');
    } finally {
      setResolvingHandoffId(null);
    }
  };

  // If not authenticated, prompt for admin token
  if (!adminToken) {
    return (
      <div className="login-view-container">
        <div className="login-card">
          <div className="login-card-header">
            <h2 className="login-card-title">Admin Portal Sign In</h2>
            <p className="login-card-subtitle">
              Enter your <code style={{ color: '#60a5fa' }}>X-Admin-Token</code> to access the refund approval and escalation management console.
            </p>
          </div>

          <form onSubmit={handleSaveToken}>
            <div style={{ marginBottom: '20px' }}>
              <label
                htmlFor="admin_token_input"
                style={{
                  display: 'block',
                  fontSize: '0.85rem',
                  fontWeight: 600,
                  marginBottom: '8px',
                  color: 'var(--text-muted)',
                }}
              >
                Admin Secret Token
              </label>
              <input
                id="admin_token_input"
                type="password"
                className="chat-input-field"
                style={{ width: '100%' }}
                placeholder="Enter ADMIN_TOKEN from environment..."
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                autoFocus
              />
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '6px' }}>
                Stored strictly in browser sessionStorage. Never exposed to customer chats.
              </div>
            </div>

            <button type="submit" className="btn-primary-block">
              Authenticate & Access Portal
            </button>
          </form>
        </div>
      </div>
    );
  }

  // Parse transcript messages safely
  const parseTranscript = (rawTranscript) => {
    if (!rawTranscript) return [];
    if (Array.isArray(rawTranscript)) return rawTranscript;
    if (typeof rawTranscript === 'string') {
      try {
        const parsed = JSON.parse(rawTranscript);
        if (Array.isArray(parsed)) return parsed;
        return [{ role: 'system', content: rawTranscript }];
      } catch {
        return [{ role: 'system', content: rawTranscript }];
      }
    }
    if (typeof rawTranscript === 'object') {
      return [rawTranscript];
    }
    return [];
  };

  const pendingRefundsCount = refunds.filter((r) => r.status === 'pending').length;
  const openHandoffsCount = handoffs.filter((h) => h.status === 'open').length;

  return (
    <div className="admin-container">
      {/* Admin Header */}
      <div className="admin-header">
        <div className="admin-title-group">
          <h2>Admin Operations Console</h2>
          <span className="admin-badge">AUTHORIZED</span>
        </div>

        <button type="button" className="btn-secondary" onClick={handleSignOut}>
          Sign Out
        </button>
      </div>

      {/* Admin Tabs */}
      <div className="admin-tabs">
        <button
          type="button"
          className={`admin-tab-btn ${activeTab === 'refunds' ? 'active' : ''}`}
          onClick={() => setActiveTab('refunds')}
        >
          <span>💰 Refund Approvals</span>
          {pendingRefundsCount > 0 && (
            <span className="count-pill">{pendingRefundsCount}</span>
          )}
        </button>
        <button
          type="button"
          className={`admin-tab-btn ${activeTab === 'handoffs' ? 'active' : ''}`}
          onClick={() => setActiveTab('handoffs')}
        >
          <span>🧑‍💼 Escalation Handoff Inbox</span>
          {openHandoffsCount > 0 && (
            <span className="count-pill">{openHandoffsCount}</span>
          )}
        </button>
      </div>

      {/* Tab 1: Refund Approvals */}
      {activeTab === 'refunds' && (
        <div className="admin-card">
          <div className="table-toolbar">
            <div className="table-filter-group">
              <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Status:</span>
              <button
                type="button"
                className={`filter-btn ${refundStatusFilter === 'pending' ? 'active' : ''}`}
                onClick={() => setRefundStatusFilter('pending')}
              >
                Pending
              </button>
              <button
                type="button"
                className={`filter-btn ${refundStatusFilter === 'all' ? 'active' : ''}`}
                onClick={() => setRefundStatusFilter('all')}
              >
                All
              </button>
            </div>

            <button
              type="button"
              className="btn-secondary"
              onClick={() => fetchRefunds()}
              disabled={isLoadingRefunds}
            >
              {isLoadingRefunds ? 'Refreshing...' : '🔄 Refresh'}
            </button>
          </div>

          <div className="table-container">
            {refunds.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">🎉</div>
                <h3>No {refundStatusFilter} refunds found</h3>
                <p>All customer refund requests have been processed.</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Order #</th>
                    <th>Customer</th>
                    <th>Item</th>
                    <th>Amount</th>
                    <th>Reason</th>
                    <th>Policy Flags</th>
                    <th>Admin Note</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {refunds.map((refund) => {
                    const isHighValue =
                      refund.flags &&
                      (refund.flags.includes('HIGH_VALUE') ||
                        refund.flags.includes('high_value'));
                    const isDeciding = !!actionInProgress[refund.id];
                    const isPending = refund.status === 'pending';

                    return (
                      <tr key={refund.id}>
                        <td>
                          <strong>#{refund.id}</strong>
                        </td>
                        <td>#{refund.order_id}</td>
                        <td>
                          <div style={{ fontWeight: 600 }}>{refund.customer_name}</div>
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                            {refund.customer_email}
                          </div>
                        </td>
                        <td>{refund.item || 'N/A'}</td>
                        <td>
                          <strong>${Number(refund.amount).toFixed(2)}</strong>
                        </td>
                        <td style={{ maxWidth: '200px' }} title={refund.reason}>
                          {refund.reason || 'No reason provided'}
                        </td>
                        <td>
                          {isHighValue ? (
                            <span className="badge-high-value">
                              ⚠️ HIGH VALUE
                            </span>
                          ) : refund.flags && refund.flags.length > 0 ? (
                            refund.flags.join(', ')
                          ) : (
                            <span style={{ color: 'var(--text-dim)' }}>Standard</span>
                          )}
                        </td>
                        <td>
                          {isPending ? (
                            <input
                              type="text"
                              className="action-note-input"
                              placeholder="Optional note..."
                              value={refundNotes[refund.id] || ''}
                              onChange={(e) =>
                                setRefundNotes((prev) => ({
                                  ...prev,
                                  [refund.id]: e.target.value,
                                }))
                              }
                              disabled={isDeciding}
                            />
                          ) : (
                            <span className={`badge-status ${refund.status}`}>
                              {refund.status}
                            </span>
                          )}
                        </td>
                        <td className="action-cell">
                          {isPending ? (
                            <div className="action-buttons">
                              <button
                                type="button"
                                className="btn-approve"
                                onClick={() => handleRefundDecision(refund.id, 'approve')}
                                disabled={isDeciding}
                              >
                                {actionInProgress[refund.id] === 'approve'
                                  ? 'Approving...'
                                  : 'Approve'}
                              </button>
                              <button
                                type="button"
                                className="btn-reject"
                                onClick={() => handleRefundDecision(refund.id, 'reject')}
                                disabled={isDeciding}
                              >
                                {actionInProgress[refund.id] === 'reject'
                                  ? 'Rejecting...'
                                  : 'Reject'}
                              </button>
                            </div>
                          ) : (
                            <span style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>
                              Decided
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Tab 2: Handoff Inbox */}
      {activeTab === 'handoffs' && (
        <div className="admin-card">
          <div className="table-toolbar">
            <div className="table-filter-group">
              <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Status:</span>
              <button
                type="button"
                className={`filter-btn ${handoffStatusFilter === 'open' ? 'active' : ''}`}
                onClick={() => setHandoffStatusFilter('open')}
              >
                Open
              </button>
              <button
                type="button"
                className={`filter-btn ${handoffStatusFilter === 'all' ? 'active' : ''}`}
                onClick={() => setHandoffStatusFilter('all')}
              >
                All
              </button>
            </div>

            <button
              type="button"
              className="btn-secondary"
              onClick={() => fetchHandoffs()}
              disabled={isLoadingHandoffs}
            >
              {isLoadingHandoffs ? 'Refreshing...' : '🔄 Refresh'}
            </button>
          </div>

          <div className="table-container">
            {handoffs.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">✅</div>
                <h3>No {handoffStatusFilter} escalations</h3>
                <p>Support queues are clear and all conversations resolved.</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Customer</th>
                    <th>Reason</th>
                    <th>Escalation Summary</th>
                    <th>Status</th>
                    <th>Created At</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {handoffs.map((handoff) => {
                    const isOpen = handoff.status === 'open';
                    const isResolving = resolvingHandoffId === handoff.id;

                    return (
                      <tr key={handoff.id}>
                        <td>
                          <strong>#{handoff.id}</strong>
                        </td>
                        <td>
                          <div style={{ fontWeight: 600 }}>{handoff.customer_name}</div>
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
                            Session: #{handoff.session_id.slice(0, 8)}
                          </div>
                        </td>
                        <td>
                          <span style={{ fontWeight: 500, color: '#f87171' }}>
                            {handoff.reason}
                          </span>
                        </td>
                        <td style={{ maxWidth: '320px' }}>{handoff.summary}</td>
                        <td>
                          <span className={`badge-status ${handoff.status}`}>
                            {handoff.status}
                          </span>
                        </td>
                        <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                          {handoff.created_at ? new Date(handoff.created_at).toLocaleString() : 'N/A'}
                        </td>
                        <td className="action-cell">
                          <div className="action-buttons">
                            <button
                              type="button"
                              className="btn-action-view"
                              onClick={() => handleOpenTranscript(handoff.id)}
                            >
                              Transcript
                            </button>
                            {isOpen && (
                              <button
                                type="button"
                                className="btn-action-resolve"
                                onClick={() => handleResolveHandoff(handoff.id)}
                                disabled={isResolving}
                              >
                                {isResolving ? 'Resolving...' : 'Resolve'}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Transcript Modal Dialog */}
      {selectedHandoffId && (
        <div className="modal-overlay" onClick={() => setSelectedHandoffId(null)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">
                Conversation Transcript — Handoff #{selectedHandoffId}
              </h3>
              <button
                type="button"
                className="toast-close"
                onClick={() => setSelectedHandoffId(null)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              {isLoadingDetail || !handoffDetail ? (
                <div style={{ padding: '30px', textAlign: 'center' }}>
                  Loading conversation history...
                </div>
              ) : (
                <>
                  <div className="info-grid">
                    <div>
                      <div className="info-item-label">Customer Name</div>
                      <div className="info-item-val">{handoffDetail.customer_name}</div>
                    </div>
                    <div>
                      <div className="info-item-label">Customer Email</div>
                      <div className="info-item-val">{handoffDetail.customer_email}</div>
                    </div>
                    <div>
                      <div className="info-item-label">Escalation Reason</div>
                      <div className="info-item-val" style={{ color: '#f87171' }}>
                        {handoffDetail.reason}
                      </div>
                    </div>
                    <div>
                      <div className="info-item-label">Current Status</div>
                      <div className="info-item-val">
                        <span className={`badge-status ${handoffDetail.status}`}>
                          {handoffDetail.status}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div>
                    <h4 style={{ fontSize: '0.85rem', marginBottom: '6px', color: 'var(--text-muted)' }}>
                      Escalation Summary
                    </h4>
                    <p style={{ fontSize: '0.9rem', color: 'var(--text-main)' }}>
                      {handoffDetail.summary}
                    </p>
                  </div>

                  <div>
                    <h4 style={{ fontSize: '0.85rem', marginBottom: '8px', color: 'var(--text-muted)' }}>
                      Full Transcript History
                    </h4>
                    <div className="transcript-container">
                      {parseTranscript(handoffDetail.transcript).map((item, idx) => {
                        const isUser = item.role === 'user';
                        return (
                          <div
                            key={idx}
                            className={`transcript-bubble ${isUser ? 'user' : 'assistant'}`}
                          >
                            <div className="transcript-speaker">
                              {isUser ? handoffDetail.customer_name : 'AI Agent'}
                            </div>
                            <div style={{ whiteSpace: 'pre-wrap' }}>
                              {item.content || item.text || JSON.stringify(item)}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </>
              )}
            </div>

            <div className="modal-footer">
              {handoffDetail && handoffDetail.status === 'open' && (
                <button
                  type="button"
                  className="btn-action-resolve"
                  onClick={() => handleResolveHandoff(selectedHandoffId)}
                  disabled={resolvingHandoffId === selectedHandoffId}
                >
                  {resolvingHandoffId === selectedHandoffId
                    ? 'Resolving...'
                    : 'Resolve Handoff'}
                </button>
              )}
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setSelectedHandoffId(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
