import React, { useState, useEffect } from 'react';
import { useDropzone } from 'react-dropzone';

interface FileUploadProps {
  onFileSelect: (file: File) => void;
  accept: string;
  selectedFile: File | null;
  label: string;
}

export const FileUpload: React.FC<FileUploadProps> = ({
  onFileSelect,
  accept,
  selectedFile,
  label,
}) => {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const isImageUpload = accept.includes('image');
  const isPdfUpload = accept.includes('pdf');

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { [accept]: [] },
    multiple: false,
    onDrop: (acceptedFiles) => {
      if (acceptedFiles.length > 0) {
        onFileSelect(acceptedFiles[0]);
      }
    },
  });

  useEffect(() => {
    if (selectedFile && isImageUpload) {
      const url = URL.createObjectURL(selectedFile);
      setPreviewUrl(url);
      return () => URL.revokeObjectURL(url);
    } else {
      setPreviewUrl(null);
    }
  }, [selectedFile, isImageUpload]);

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  return (
    <div
      {...getRootProps()}
      className={`file-upload ${isDragActive ? 'active' : ''}`}
    >
      <input {...getInputProps()} />
      
      {!selectedFile ? (
        <div className="file-upload-text">
          {isDragActive ? (
            'Drop file here...'
          ) : (
            `Click to upload or drag and drop ${label.toLowerCase()}`
          )}
        </div>
      ) : (
        <div className="file-preview">
          {isImageUpload && previewUrl && (
            <img 
              src={previewUrl} 
              alt="Preview" 
              className="preview-image"
            />
          )}
          
          <div className="file-info">
            <div className="file-name">{selectedFile.name}</div>
            <div className="file-details">
              {isPdfUpload && '📄 '}
              {isImageUpload && '🖼️ '}
              {formatFileSize(selectedFile.size)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};