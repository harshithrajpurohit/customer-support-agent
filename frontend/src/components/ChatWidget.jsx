import React, { useState, useEffect, useRef } from 'react';
import { SEEDED_CUSTOMERS } from '../config';
import { loginCustomer, sendChatMessage, getSessionMessages } from '../api';

export default function ChatWidget({ onShowToast }) {
  // Session & Customer State
  const [customer, setCustomer] = useState(() => {
    try {
      const saved = localStorage.getItem('support_customer');
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });

  const [sessionId, setSessionId] = useState(() => {
    return localStorage.getItem('support_session_id') || '';
  });

  const [selectedEmail, setSelectedEmail] = useState(SEEDED_CUSTOMERS[0].email);
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  // Chat conversation state
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [conversationStatus, setConversationStatus] = useState('answered'); // answered | awaiting_approval | handed_off
  const [lastMessageId, setLastMessageId] = useState(0);

  const messagesEndRef = useRef(null);

  // Scroll to bottom smoothly when messages change
  const scrollToBottom = () => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isSending]);

  // Persist session to local storage
  useEffect(() => {
    if (sessionId && customer) {
      localStorage.setItem('support_session_id', sessionId);
      localStorage.setItem('support_customer', JSON.stringify(customer));
    } else {
      localStorage.removeItem('support_session_id');
      localStorage.removeItem('support_customer');
    }
  }, [sessionId, customer]);

  // Handle Demo Login
  const handleLogin = async (e) => {
    if (e) e.preventDefault();
    setIsLoggingIn(true);
    try {
      const data = await loginCustomer(selectedEmail);
      setSessionId(data.session_id);
      setCustomer(data.customer);
      setMessages([]);
      setLastMessageId(0);
      setConversationStatus('answered');

      // Add welcoming greeting
      const greetingMsg = {
        id: -1,
        role: 'assistant',
        content: `Hello ${data.customer.name}! 👋 I am your AI support assistant. I can help track your orders, check policies, or assist with returns and refunds. What can I help you with today?`,
        intent: 'greeting',
        created_at: new Date().toISOString(),
      };
      setMessages([greetingMsg]);
    } catch (err) {
      onShowToast(`Login failed: ${err.message}`, 'error');
    } finally {
      setIsLoggingIn(false);
    }
  };

  // Handle Logout / Switch Customer
  const handleLogout = () => {
    setCustomer(null);
    setSessionId('');
    setMessages([]);
    setLastMessageId(0);
    setConversationStatus('answered');
  };

  // Initial load of messages when session exists
  useEffect(() => {
    if (!sessionId) return;

    let isMounted = true;
    async function loadInitialMessages() {
      try {
        const res = await getSessionMessages(sessionId);
        if (!isMounted) return;
        if (res && res.messages && res.messages.length > 0) {
          setMessages(res.messages);
          const maxId = Math.max(...res.messages.map((m) => m.id), 0);
          setLastMessageId(maxId);

          // Check if any message indicates awaiting_approval or handed_off
          const lastMsg = res.messages[res.messages.length - 1];
          if (lastMsg && lastMsg.content) {
            if (lastMsg.content.toLowerCase().includes('under review') || lastMsg.content.toLowerCase().includes('pending review')) {
              setConversationStatus('awaiting_approval');
            } else if (lastMsg.content.toLowerCase().includes('human support agent') || lastMsg.content.toLowerCase().includes('transferred to a specialist')) {
              setConversationStatus('handed_off');
            }
          }
        } else if (customer) {
          // Default greeting
          setMessages([
            {
              id: -1,
              role: 'assistant',
              content: `Hello ${customer.name}! 👋 How can I help you with your orders or support requests today?`,
              intent: 'greeting',
              created_at: new Date().toISOString(),
            },
          ]);
        }
      } catch (err) {
        // network warning
        console.warn('Could not load initial messages:', err);
      }
    }

    loadInitialMessages();
    return () => {
      isMounted = false;
    };
  }, [sessionId, customer]);

  // Polling every 3 seconds for asynchronous messages (admin decisions, handoff replies)
  useEffect(() => {
    if (!sessionId) return;

    const interval = setInterval(async () => {
      try {
        const res = await getSessionMessages(sessionId, lastMessageId > 0 ? lastMessageId : undefined);
        if (res && res.messages && res.messages.length > 0) {
          setMessages((prev) => {
            const existingIds = new Set(prev.map((m) => m.id));
            const newOnes = res.messages.filter((m) => !existingIds.has(m.id));
            if (newOnes.length === 0) return prev;
            return [...prev, ...newOnes];
          });

          const maxId = Math.max(...res.messages.map((m) => m.id), lastMessageId);
          setLastMessageId(maxId);

          // Check if newly arrived async messages resolve approval
          for (const msg of res.messages) {
            const lower = (msg.content || '').toLowerCase();
            if (lower.includes('approved') || lower.includes('rejected')) {
              setConversationStatus('answered');
            }
          }
        }
      } catch (err) {
        // Silently handle polling glitch, log in console
        console.warn('Polling error:', err);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [sessionId, lastMessageId]);

  // Send Message
  const handleSendMessage = async (textToSend) => {
    const text = (textToSend || inputValue).trim();
    if (!text || isSending || !sessionId) return;

    // Optimistically add user message
    const tempUserMsg = {
      id: Date.now(),
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, tempUserMsg]);
    setInputValue('');
    setIsSending(true);

    try {
      const res = await sendChatMessage(sessionId, text);
      
      // Update status banner if awaiting approval or handed off
      if (res.status) {
        setConversationStatus(res.status);
      }

      // Add assistant response
      const assistantMsg = {
        id: Date.now() + 1,
        role: 'assistant',
        content: res.reply,
        intent: res.intent,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      onShowToast(`Failed to send message: ${err.message}`, 'error');
      // Add error notification message in chat
      const errorMsg = {
        id: Date.now() + 2,
        role: 'assistant',
        content: `⚠️ Sorry, we encountered a network issue communicating with the server: ${err.message}. Please try again.`,
        intent: 'error',
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsSending(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const formatTime = (isoString) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  // If customer is not logged in, show demo login screen
  if (!customer || !sessionId) {
    return (
      <div className="login-view-container">
        <div className="login-card">
          <div className="login-card-header">
            <h2 className="login-card-title">Welcome to Customer Support</h2>
            <p className="login-card-subtitle">
              Select one of the demo customer accounts to start a support session.
            </p>
          </div>

          <form onSubmit={handleLogin}>
            <div className="customer-options-list">
              {SEEDED_CUSTOMERS.map((cust) => {
                const isSelected = selectedEmail === cust.email;
                return (
                  <div
                    key={cust.email}
                    className={`customer-option-item ${isSelected ? 'selected' : ''}`}
                    onClick={() => setSelectedEmail(cust.email)}
                  >
                    <div className="avatar-circle">
                      {cust.name.charAt(0)}
                    </div>
                    <div className="customer-option-info">
                      <div className="customer-option-name">{cust.name}</div>
                      <div className="customer-option-email">{cust.email}</div>
                      <div className="customer-option-desc">{cust.desc}</div>
                    </div>
                    <input
                      type="radio"
                      name="customer_select"
                      checked={isSelected}
                      onChange={() => setSelectedEmail(cust.email)}
                    />
                  </div>
                );
              })}
            </div>

            <button
              type="submit"
              className="btn-primary-block"
              disabled={isLoggingIn}
            >
              {isLoggingIn ? 'Logging in...' : 'Start Support Chat'}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-card">
      {/* Top Customer Info Bar */}
      <div className="chat-header">
        <div className="customer-profile">
          <div className="avatar-circle">
            {customer.name ? customer.name.charAt(0) : 'U'}
          </div>
          <div className="customer-meta">
            <span className="customer-name">
              {customer.name}
              <span className="session-badge" title={`Session ID: ${sessionId}`}>
                #{sessionId.slice(0, 8)}
              </span>
            </span>
            <span className="customer-email">{customer.email || 'Logged in'}</span>
          </div>
        </div>

        <div className="chat-header-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={handleLogout}
            title="Switch to another demo customer"
          >
            Switch Customer
          </button>
        </div>
      </div>

      {/* Status Banner when awaiting approval or handed off */}
      {conversationStatus === 'awaiting_approval' && (
        <div className="status-banner awaiting_approval">
          <span className="banner-icon">⏳</span>
          <div>
            <div className="banner-text-title">Your refund request is under review by our team</div>
            <div className="banner-text-desc">
              An administrator will review your request shortly. The decision will appear here automatically.
            </div>
          </div>
        </div>
      )}

      {conversationStatus === 'handed_off' && (
        <div className="status-banner handed_off">
          <span className="banner-icon">🧑‍💼</span>
          <div>
            <div className="banner-text-title">You have been connected to a human support agent</div>
            <div className="banner-text-desc">
              Your inquiry has been escalated. A support specialist has received your transcript and will assist you.
            </div>
          </div>
        </div>
      )}

      {/* Messages Scroll Area */}
      <div className="messages-container">
        {messages.map((msg, index) => {
          const isUser = msg.role === 'user';
          return (
            <div
              key={msg.id ? `msg-${msg.id}` : `msg-idx-${index}`}
              className={`message-row ${isUser ? 'user' : 'assistant'}`}
            >
              <div className="message-bubble">
                <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
                <div className="message-meta">
                  {msg.intent && !isUser && (
                    <span className="intent-pill">{msg.intent}</span>
                  )}
                  <span>{formatTime(msg.created_at)}</span>
                </div>
              </div>
            </div>
          );
        })}

        {isSending && (
          <div className="message-row assistant">
            <div className="typing-indicator" title="Support agent is typing...">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Quick Prompt Chips */}
      <div className="quick-prompts">
        <button
          type="button"
          className="prompt-chip"
          onClick={() => handleSendMessage('What is the status of my recent orders?')}
          disabled={isSending}
        >
          📦 Where is my order?
        </button>
        <button
          type="button"
          className="prompt-chip"
          onClick={() => handleSendMessage('Can I request a refund for order #1002?')}
          disabled={isSending}
        >
          💰 Refund request for #1002
        </button>
        <button
          type="button"
          className="prompt-chip"
          onClick={() => handleSendMessage('What is your refund and return policy?')}
          disabled={isSending}
        >
          📜 Return Policy
        </button>
        <button
          type="button"
          className="prompt-chip"
          onClick={() => handleSendMessage('I would like to speak to a human representative please.')}
          disabled={isSending}
        >
          🧑‍💼 Speak to human
        </button>
      </div>

      {/* Chat Input Bar */}
      <div className="chat-input-bar">
        <form
          className="chat-input-form"
          onSubmit={(e) => {
            e.preventDefault();
            handleSendMessage();
          }}
        >
          <input
            type="text"
            className="chat-input-field"
            placeholder="Type your message... (Press Enter to send)"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isSending}
            maxLength={1000}
          />
          <button
            type="submit"
            className="btn-send"
            disabled={isSending || !inputValue.trim()}
          >
            <span>Send</span>
            <span>➤</span>
          </button>
        </form>
      </div>
    </div>
  );
}
