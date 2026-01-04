import React, { useState, useEffect } from 'react';

interface ProgressBarProps {
  message: string;
  logs: string[];
  isActive: boolean;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ message, logs, isActive }) => {
  return (
    <div className="progress-section">
      <div className="progress-bar-flowing">
        <div className={`progress-flow ${isActive ? 'active' : ''}`} />
      </div>
      <div className="progress-status">
        {message}
      </div>
      
      {logs.length > 0 && (
        <div className="logs-container">
          <div className="logs-header">Processing Details:</div>
          <div className="logs-box">
            {logs.map((log, index) => (
              <div key={index} className="log-line">
                {log}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};