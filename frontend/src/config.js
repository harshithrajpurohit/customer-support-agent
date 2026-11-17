// Configuration for Support Agent Frontend
export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const SEEDED_CUSTOMERS = [
  { name: 'Aarav Sharma', email: 'aarav@example.com', desc: 'Frequent buyer (Orders #1001, #1002, #1003)' },
  { name: 'Priya Verma', email: 'priya@example.com', desc: 'Active shopper (Orders #1004, #1005)' },
  { name: 'Rohan Gupta', email: 'rohan@example.com', desc: 'Tech buyer (Orders #1006, #1007, #1008)' },
  { name: 'Neha Iyer', email: 'neha@example.com', desc: 'Recent buyer (Orders #1009, #1010, #1011, #1012)' },
];
