'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation } from '@tanstack/react-query';
import * as api from '@/lib/api';
import { useCommodityGroups } from '@/lib/hooks/useCommodityGroups';
import { Button, Input, Alert, Loader } from '@/components/base';

interface OrderLine {
  // Stable id so a line keeps its suggestions when other lines are removed.
  key: number;
  positionDescription: string;
  unitPrice: number;
  amount: number;
  unit: string;
  totalPrice: number;
}

// Units that all mean "one item", so a catalog "piece" price compares to a quoted "ea.".
const ITEM_UNITS = new Set(['', 'ea', 'each', 'pc', 'pcs', 'piece', 'pieces', 'unit', 'units', 'item', 'items']);
const normalizeUnit = (unit: string) => {
  const value = unit.trim().toLowerCase().replace(/\.$/, '');
  return ITEM_UNITS.has(value) ? 'item' : value.replace(/s$/, '');
};
const formatPrice = (price: number, currency: string) =>
  `${currency === 'USD' ? '$' : `${currency} `}${price.toFixed(2)}`;

export default function NewRequestPage() {
  const router = useRouter();
  const { data: commodityGroups } = useCommodityGroups();

  // Form state
  const [requestorName, setRequestorName] = useState('');
  const [title, setTitle] = useState('');
  const [vendorName, setVendorName] = useState('');
  const [vatId, setVatId] = useState('');
  const [department, setDepartment] = useState('');
  const [commodityGroupId, setCommodityGroupId] = useState<number | null>(null);
  const [orderLines, setOrderLines] = useState<OrderLine[]>([]);
  const [totalCost, setTotalCost] = useState<number>(0);

  // UI state
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [warning, setWarning] = useState('');
  const [extractionWarnings, setExtractionWarnings] = useState<string[]>([]);
  const [extracting, setExtracting] = useState(false);

  // Catalog suggestions per order line, keyed by the line's stable key
  const [suggestions, setSuggestions] = useState<Record<number, api.ArticleSuggestion[]>>({});
  const nextLineKey = useRef(0);
  const suggestionTimers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});
  const latestQuery = useRef<Record<number, string>>({});

  const loadSuggestions = async (key: number, description: string) => {
    latestQuery.current[key] = description;
    if (description.trim().length < 3) {
      setSuggestions((current) => ({ ...current, [key]: [] }));
      return;
    }
    try {
      const result = await api.articles.suggestions(description);
      // Ignore an answer for text the user has changed since.
      if (latestQuery.current[key] === description) {
        setSuggestions((current) => ({ ...current, [key]: result }));
      }
    } catch {
      // Suggestions are optional; the form works without them.
    }
  };

  // PDF parsing mutation
  const parsePDFMutation = useMutation({
    mutationFn: async (file: File) => {
      const result = await api.pdf.parse(file);
      if (!result.text) {
        throw new Error('Failed to parse PDF');
      }
      return result.text;
    },
  });

  // Handle file upload and extraction
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const uploadedFile = e.target.files?.[0];
    if (!uploadedFile) return;

    if (uploadedFile.type !== 'application/pdf') {
      setError('Please upload a PDF file');
      return;
    }

    setFile(uploadedFile);
    setError('');
    setSuccess('');
    setWarning('');
    setExtractionWarnings([]);
    setExtracting(true);

    try {
      // Step 1: Parse PDF
      const pdfText = await parsePDFMutation.mutateAsync(uploadedFile);

      // Step 2: Extract structured data AND classify commodity group in one AI call
      const extractionResult = await api.extraction.extract(pdfText);
      
      if (!extractionResult.success || !extractionResult.data) {
        throw new Error(extractionResult.error || 'Failed to extract data');
      }

      const extracted = extractionResult.data;

      // Step 3: Auto-fill form (including commodity group from extraction)
      setTitle(extracted.title || '');
      setVendorName(extracted.vendorName || '');
      setVatId(extracted.vatId || '');
      setDepartment(extracted.department || '');
      // Unknown numbers arrive as null; the form edits plain numbers.
      const lines = (extracted.orderLines || []).map((line) => ({
        key: nextLineKey.current++,
        positionDescription: line.positionDescription,
        unit: line.unit,
        unitPrice: line.unitPrice ?? 0,
        amount: line.amount ?? 0,
        totalPrice: line.totalPrice ?? 0,
      }));
      setOrderLines(lines);
      setSuggestions({});
      lines.forEach((line) => loadSuggestions(line.key, line.positionDescription));
      setTotalCost(extracted.totalCost || 0);
      
      // Set commodity group (now included in extraction)
      if (extracted.commodityGroupId) {
        setCommodityGroupId(extracted.commodityGroupId);
      }

      // Show warnings for missing fields
      if (extractionResult.missingFields && extractionResult.missingFields.length > 0) {
        setWarning(
          `⚠️ Could not extract: ${extractionResult.missingFields.join(', ')}. Please fill in these fields manually.`
        );
      }

      setExtractionWarnings(extractionResult.warnings || []);
      setSuccess('✓ PDF extracted successfully! Review and submit.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      console.error('Extraction error:', err);
      setError(err instanceof Error ? err.message : 'Failed to extract data from PDF');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } finally {
      setExtracting(false);
    }
  };

  // Add order line manually
  const addOrderLine = () => {
    setOrderLines([
      ...orderLines,
      {
        key: nextLineKey.current++,
        positionDescription: '',
        unitPrice: 0,
        amount: 1,
        unit: '',
        totalPrice: 0,
      },
    ]);
  };

  // Update order line
  const updateOrderLine = (index: number, field: keyof OrderLine, value: string | number) => {
    const updated = [...orderLines];
    updated[index] = { ...updated[index], [field]: value };
    
    // Auto-calculate total price
    if (field === 'unitPrice' || field === 'amount') {
      updated[index].totalPrice = updated[index].unitPrice * updated[index].amount;
    }
    
    setOrderLines(updated);

    // Update total cost
    const newTotal = updated.reduce((sum, line) => sum + line.totalPrice, 0);
    setTotalCost(newTotal);

    // Look up catalog articles once the user pauses typing
    if (field === 'positionDescription') {
      const key = updated[index].key;
      clearTimeout(suggestionTimers.current[key]);
      suggestionTimers.current[key] = setTimeout(() => loadSuggestions(key, String(value)), 400);
    }
  };

  // Use a catalog article: description, negotiated unit price and unit; amount stays
  const applySuggestion = (index: number, suggestion: api.ArticleSuggestion) => {
    const updated = [...orderLines];
    const line = updated[index];
    const unitPrice = Number(suggestion.unit_price);
    updated[index] = {
      ...line,
      positionDescription: suggestion.description,
      unitPrice,
      unit: suggestion.unit,
      totalPrice: unitPrice * line.amount,
    };
    setOrderLines(updated);
    setTotalCost(updated.reduce((sum, l) => sum + l.totalPrice, 0));
    clearTimeout(suggestionTimers.current[line.key]);
    latestQuery.current[line.key] = suggestion.description;
    setSuggestions((current) => ({ ...current, [line.key]: [] }));
  };

  // Remove order line
  const removeOrderLine = (index: number) => {
    const removedKey = orderLines[index].key;
    clearTimeout(suggestionTimers.current[removedKey]);
    setSuggestions((current) => {
      const next = { ...current };
      delete next[removedKey];
      return next;
    });
    const updated = orderLines.filter((_, i) => i !== index);
    setOrderLines(updated);
    const newTotal = updated.reduce((sum, line) => sum + line.totalPrice, 0);
    setTotalCost(newTotal);
  };

  // Submit form
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setWarning('');
    setExtractionWarnings([]);

    // Validation with scroll to top on error
    if (!commodityGroupId) {
      setError('Please select a commodity group');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    if (orderLines.length === 0) {
      setError('Please add at least one order line');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    try {
      const created = await api.requests.create({
        requestorName,
        titleShortDescription: title,
        vendorName,
        vatId,
        commodityGroupId: commodityGroupId,
        totalCost,
        department,
        orderLines: orderLines.map((line) => ({
          positionDescription: line.positionDescription,
          unitPrice: line.unitPrice,
          amount: line.amount,
          unit: line.unit,
          totalPrice: line.totalPrice,
        })),
      });

      // Attach the original PDF (if one was uploaded) so it shows on the detail page.
      if (file) {
        try {
          await api.requests.uploadDocument(created.id, file);
        } catch {
          // Non-fatal — the request itself was created.
        }
      }

      setSuccess('Request created successfully! Redirecting...');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      setTimeout(() => {
        router.push('/dashboard/requests');
        router.refresh();
      }, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create request');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">New Procurement Request</h1>
        <p className="text-gray-600 mt-2">
          Upload a vendor offer PDF or fill in the details manually
        </p>
      </div>

      {error && <Alert variant="error" className="mb-6">{error}</Alert>}
      {warning && <Alert variant="warning" className="mb-6">{warning}</Alert>}
      {success && <Alert variant="success" className="mb-6">{success}</Alert>}
      {extractionWarnings.length > 0 && (
        <Alert variant="warning" className="mb-6">
          <p className="font-medium">Please review the extracted data:</p>
          <ul className="list-disc pl-5 mt-1 space-y-0.5">
            {extractionWarnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </Alert>
      )}

      <form onSubmit={handleSubmit} className="space-y-8">
        {/* PDF Upload Section */}
        <div className="lio-card p-6">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Upload Vendor Offer (Optional)</h2>
          <p className="text-sm text-gray-600 mb-4">
            Upload a PDF to automatically extract vendor details and order items
          </p>

          <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-accent-deep/50 transition-colors">
            <input
              type="file"
              accept="application/pdf"
              onChange={handleFileChange}
              className="hidden"
              id="pdf-upload"
              disabled={extracting}
            />
            <label htmlFor="pdf-upload" className="cursor-pointer">
              {extracting ? (
                <div className="flex flex-col items-center">
                  <Loader size="lg" />
                  <p className="mt-4 text-sm text-gray-600">Extracting data from PDF...</p>
                </div>
              ) : file ? (
                <div>
                  <svg
                    className="mx-auto h-12 w-12 text-green-500"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M5 13l4 4L19 7"
                    />
                  </svg>
                  <p className="mt-2 text-sm font-medium text-gray-900">{file.name}</p>
                  <p className="mt-1 text-xs text-gray-500">Click to upload a different file</p>
                </div>
              ) : (
                <div>
                  <svg
                    className="mx-auto h-12 w-12 text-gray-400"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                    />
                  </svg>
                  <p className="mt-2 text-sm text-gray-600">
                    Click to upload or drag and drop
                  </p>
                  <p className="mt-1 text-xs text-gray-500">PDF up to 10MB</p>
                </div>
              )}
            </label>
          </div>
        </div>

        {/* Request Details */}
        <div className="lio-card p-6 space-y-4">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Request Details</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Requestor Name *"
              value={requestorName}
              onChange={(e) => setRequestorName(e.target.value)}
              required
              placeholder="John Doe"
            />

            <Input
              label="Department"
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              placeholder="IT Department"
            />
          </div>

          <Input
            label="Title / Short Description *"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            placeholder="Adobe Creative Cloud Subscription"
          />
        </div>

        {/* Vendor Information */}
        <div className="lio-card p-6 space-y-4">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Vendor Information</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Vendor Name *"
              value={vendorName}
              onChange={(e) => setVendorName(e.target.value)}
              required
              placeholder="Adobe Systems"
            />

            <Input
              label="Tax ID"
              value={vatId}
              onChange={(e) => setVatId(e.target.value)}
              placeholder="12-3456789"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Commodity Group *
            </label>
            <select
              value={commodityGroupId || ''}
              onChange={(e) => setCommodityGroupId(Number(e.target.value))}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-gray-900 focus:ring-2 focus:ring-accent-deep/60 focus:border-accent-deep/40"
            >
              <option value="">
                {commodityGroups ? 'Select a commodity group' : 'Loading...'}
              </option>
              {commodityGroups?.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.id} - {group.category} - {group.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Order Lines */}
        <div className="lio-card p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold tracking-tight text-ink">Order Lines *</h2>
            <Button type="button" onClick={addOrderLine} variant="outline" size="sm">
              + Add Line
            </Button>
          </div>

          {orderLines.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              No order lines yet. Add one to get started.
            </div>
          ) : (
            <div className="space-y-4">
              {orderLines.map((line, index) => (
                <div key={line.key} className="border border-gray-200 rounded-lg p-4">
                  <div className="flex items-start justify-between mb-4">
                    <h3 className="text-sm font-medium text-gray-700">Line {index + 1}</h3>
                    <button
                      type="button"
                      onClick={() => removeOrderLine(index)}
                      className="text-red-600 hover:text-red-700 text-sm"
                    >
                      Remove
                    </button>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
                    <div className="lg:col-span-2">
                      <Input
                        label="Description"
                        value={line.positionDescription}
                        onChange={(e) =>
                          updateOrderLine(index, 'positionDescription', e.target.value)
                        }
                        required
                        placeholder="Item description"
                      />
                    </div>

                    <Input
                      label="Unit Price ($)"
                      type="number"
                      step="0.01"
                      value={line.unitPrice}
                      onChange={(e) =>
                        updateOrderLine(index, 'unitPrice', parseFloat(e.target.value) || 0)
                      }
                      required
                    />

                    <Input
                      label="Amount"
                      type="number"
                      step="0.01"
                      value={line.amount}
                      onChange={(e) =>
                        updateOrderLine(index, 'amount', parseFloat(e.target.value) || 1)
                      }
                      required
                    />

                    <Input
                      label="Unit"
                      value={line.unit}
                      onChange={(e) => updateOrderLine(index, 'unit', e.target.value)}
                      required
                      placeholder="pcs, licenses, etc."
                    />
                  </div>

                  <div className="mt-2 text-right">
                    <span className="text-sm text-gray-600">Total: </span>
                    <span className="text-lg font-semibold tracking-tight text-ink">
                      ${line.totalPrice.toFixed(2)}
                    </span>
                  </div>

                  {(suggestions[line.key] || []).length > 0 && (
                    <div className="mt-3 border-t border-gray-100 pt-3">
                      <p className="text-xs font-medium text-gray-500 mb-1.5">
                        Negotiated catalog prices
                      </p>
                      <ul className="space-y-1.5">
                        {suggestions[line.key].map((suggestion, i) => {
                          const price = Number(suggestion.unit_price);
                          // Only compare prices quoted in the same unit.
                          const saving =
                            line.unitPrice > 0 &&
                            price < line.unitPrice &&
                            normalizeUnit(line.unit) === normalizeUnit(suggestion.unit)
                              ? line.unitPrice - price
                              : null;
                          return (
                            <li key={i} className="flex items-center justify-between gap-3 text-sm">
                              <span className="text-gray-700 min-w-0">
                                {suggestion.description} · {suggestion.supplier_name ?? 'Unknown supplier'} ·{' '}
                                {formatPrice(price, suggestion.currency)} / {suggestion.unit}
                                {saving !== null && (
                                  <span className="ml-2 font-medium text-green-700">
                                    saves {formatPrice(saving, suggestion.currency)} / unit
                                  </span>
                                )}
                              </span>
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                onClick={() => applySuggestion(index, suggestion)}
                              >
                                Use
                              </Button>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Total Cost */}
        <div className="bg-accent-soft/20 border-2 border-accent/40 rounded-lg p-6">
          <div className="flex items-center justify-between">
            <span className="text-lg font-medium text-gray-900">Total Cost</span>
            <span className="text-3xl font-bold text-accent-deep">
              ${totalCost.toFixed(2)}
            </span>
          </div>
        </div>

        {/* Submit */}
        <div className="flex items-center justify-end space-x-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => router.back()}
          >
            Cancel
          </Button>
          <Button type="submit" size="lg">
            Create Request
          </Button>
        </div>
      </form>
    </div>
  );
}

