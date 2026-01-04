import React, { useEffect, useMemo, useState } from 'react';
import { FileUpload } from './components/FileUpload';
import { ProgressBar } from './components/ProgressBar';
import { apiService } from './api';
import { JobStatus, SlideConfig, UploadedFiles } from './types';
import postergenLogo from './logo.jpg';

function App() {
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [config, setConfig] = useState<SlideConfig>({
    model_name_t: '',
    model_name_v: '',
  });
  const [files, setFiles] = useState<UploadedFiles>({
    pdf_file: null,
  });
  const [currentJob, setCurrentJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);

  // Load models on startup
  useEffect(() => {
    const loadModels = async () => {
      try {
        const models = await apiService.getModels();
        setAvailableModels(models);
        if (models.length > 0) {
          setConfig((prev) => ({
            ...prev,
            model_name_t: prev.model_name_t || models[0],
            model_name_v: prev.model_name_v || models[0],
          }));
        }
      } catch {
        setError('Failed to load available models');
      }
    };
    loadModels();
  }, []);

  // Poll job status/logs while running
  useEffect(() => {
    if (!currentJob || currentJob.status === 'completed' || currentJob.status === 'failed') {
      return;
    }

    const pollInterval = window.setInterval(async () => {
      try {
        const [status, logLines] = await Promise.all([
          apiService.getJobStatus(currentJob.job_id),
          apiService.getJobLogs(currentJob.job_id),
        ]);

        setCurrentJob(status);
        setLogs(logLines);

        if (status.status === 'failed') {
          setError(status.error || 'Job failed');
          setIsSubmitting(false);
        } else if (status.status === 'completed') {
          setIsSubmitting(false);
        }
      } catch {
        setError('Failed to check job status');
        setIsSubmitting(false);
      }
    }, 2000);

    return () => window.clearInterval(pollInterval);
  }, [currentJob]);

  const handleConfigChange = (field: keyof SlideConfig, value: string | number | boolean) => {
    setConfig((prev) => ({ ...prev, [field]: value } as SlideConfig));
  };

  const handleFileChange = (field: keyof UploadedFiles, file: File) => {
    setFiles((prev) => ({ ...prev, [field]: file }));
    setError(null);
  };

  const validateForm = (): string | null => {
    if (!files.pdf_file) return 'Please upload a PDF paper';
    if (!config.model_name_t || !config.model_name_v) return 'Please select models';
    return null;
  };

  const canSubmit = useMemo(() => {
    return Boolean(files.pdf_file && config.model_name_t && config.model_name_v && !isSubmitting);
  }, [files.pdf_file, config.model_name_t, config.model_name_v, isSubmitting]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const validationError = validateForm();
    if (validationError) {
      setError(validationError);
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setCurrentJob(null);
    setLogs([]);

    try {
      const jobStatus = await apiService.generateSlides(config, files);
      setCurrentJob(jobStatus);
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to start slide generation');
      setIsSubmitting(false);
    }
  };

  const handleReset = () => {
    setError(null);
    setIsSubmitting(false);
    setCurrentJob(null);
    setLogs([]);
    setFiles({ pdf_file: null });
  };

  return (
    <div className="container">
      <div className="header">
        <h1 style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <img src={postergenLogo} alt="SlideGen Logo" style={{ height: '1.5em', marginRight: '0.5em' }} />
          SlideGen WebUI
        </h1>
        <p>Generate scientific slides (PPTX) from a PDF paper</p>
      </div>

      <form onSubmit={handleSubmit} className="main-form">
        <div className="form-section">
          <h3 className="section-title">Upload</h3>
          <div className="form-group">
            <label>PDF Paper</label>
            <FileUpload
              label="PDF Paper"
              accept="application/pdf"
              selectedFile={files.pdf_file}
              onFileSelect={(file) => handleFileChange('pdf_file', file)}
            />
          </div>
        </div>

        <div className="form-section">
          <h3 className="section-title">Model</h3>

          <div className="form-row">
            <div className="form-group">
              <label>Text Model</label>
              <select
                value={config.model_name_t}
                onChange={(e) => handleConfigChange('model_name_t', e.target.value)}
              >
                {availableModels.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Vision Model</label>
              <select
                value={config.model_name_v}
                onChange={(e) => handleConfigChange('model_name_v', e.target.value)}
              >
                {availableModels.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
          <button type="submit" className="button" disabled={!canSubmit} style={{ width: 'auto', minWidth: 200 }}>
            {isSubmitting ? 'Generating Slides…' : 'Generate Slides'}
          </button>
          <button type="button" className="button secondary" onClick={handleReset} style={{ width: 'auto', minWidth: 120 }}>
            Reset
          </button>
        </div>

        {error && <div className="error-message">{error}</div>}

        {currentJob && currentJob.status !== 'failed' && (
          <ProgressBar
            message={`${currentJob.message}${typeof currentJob.progress === 'number' ? ` (${currentJob.progress}%)` : ''}`}
            logs={logs}
            isActive={currentJob.status === 'processing' || currentJob.status === 'pending'}
          />
        )}

        {currentJob && currentJob.status === 'completed' && (
          <div className="download-section">
            <div className="success-message">Slide generation completed successfully.</div>
            <a href={apiService.getDownloadUrl(currentJob.job_id)} className="download-button" download>
              Download PPTX
            </a>
          </div>
        )}
      </form>
    </div>
  );
}

export default App;
