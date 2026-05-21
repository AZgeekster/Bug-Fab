<?php

declare(strict_types=1);

namespace BugFab\Laravel\Http\Requests;

use BugFab\Laravel\Enums\ReportType;
use BugFab\Laravel\Enums\Severity;
use BugFab\Laravel\Support\Errors;
use Illuminate\Contracts\Validation\Validator;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Http\Exceptions\HttpResponseException;
use Illuminate\Http\JsonResponse;
use Illuminate\Validation\Rule;

/**
 * Validates the multipart intake payload.
 *
 * Multipart has two parts (per PROTOCOL.md § Request):
 *  - "metadata"   — JSON-encoded string (parsed in prepareForValidation)
 *  - "screenshot" — PNG file
 *
 * We pre-decode the JSON into the request bag so Laravel's validation rules
 * can address nested fields directly (metadata.title, metadata.severity, ...).
 */
class StoreBugReportRequest extends FormRequest
{
    /** Cached parse of the metadata field — set in prepareForValidation. */
    private ?array $parsedMetadata = null;
    private bool $metadataParseFailed = false;
    private string $metadataParseError = '';

    public function authorize(): bool
    {
        // Mount-point auth is the gate per PROTOCOL.md § Auth. The form
        // request itself is intentionally permissive.
        return true;
    }

    protected function prepareForValidation(): void
    {
        $raw = $this->input('metadata');
        if (! is_string($raw) || $raw === '') {
            $this->merge(['metadata' => []]);
            return;
        }
        try {
            $decoded = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        } catch (\JsonException $e) {
            $this->metadataParseFailed = true;
            $this->metadataParseError = $e->getMessage();
            // Hand a sentinel to validation so the after() hook can short-
            // circuit with a 400 rather than enumerating 422 field errors
            // on a structure that's not even JSON.
            $this->merge(['metadata' => []]);
            return;
        }
        if (! is_array($decoded)) {
            $this->metadataParseFailed = true;
            $this->metadataParseError = 'metadata must be a JSON object';
            $this->merge(['metadata' => []]);
            return;
        }
        $this->parsedMetadata = $decoded;
        $this->merge(['metadata' => $decoded]);
    }

    public function rules(): array
    {
        return [
            'metadata'                    => ['required', 'array'],
            'metadata.protocol_version'   => ['required', 'string'],
            'metadata.title'              => ['required', 'string', 'min:1', 'max:200'],
            'metadata.client_ts'          => ['required', 'string', 'min:1'],
            'metadata.report_type'        => ['sometimes', Rule::enum(ReportType::class)],
            'metadata.description'        => ['sometimes', 'string'],
            'metadata.expected_behavior'  => ['sometimes', 'string'],
            // Rule::enum() enforces the strict severity whitelist with 422 on
            // mismatch — exactly the contract PROTOCOL.md § Severity enum
            // requires. Custom Validator::extend would diverge from Laravel's
            // built-in enum support and lose IDE support.
            'metadata.severity'           => ['sometimes', Rule::enum(Severity::class)],
            'metadata.tags'               => ['sometimes', 'array'],
            'metadata.tags.*'             => ['string'],
            'metadata.reporter'           => ['sometimes', 'array'],
            'metadata.reporter.name'      => ['sometimes', 'string', 'max:256'],
            'metadata.reporter.email'     => ['sometimes', 'string', 'max:256'],
            'metadata.reporter.user_id'   => ['sometimes', 'string', 'max:256'],
            'metadata.context'            => ['sometimes', 'array'],

            'screenshot'                  => ['required', 'file'],
        ];
    }

    protected function passedValidation(): void
    {
        // If JSON decoding failed earlier we shouldn't have reached here, but
        // belt-and-suspenders: surface the parse error as 400 instead of 422.
        if ($this->metadataParseFailed) {
            throw new HttpResponseException(
                Errors::json(Errors::VALIDATION_ERROR, $this->metadataParseError, 400)
            );
        }
    }

    /**
     * Convert validator failures into the protocol error envelope (422
     * schema_error) instead of Laravel's default { errors: { field: [...] } }.
     */
    protected function failedValidation(Validator $validator): void
    {
        if ($this->metadataParseFailed) {
            throw new HttpResponseException(
                Errors::json(Errors::VALIDATION_ERROR, $this->metadataParseError, 400)
            );
        }

        $errors = $validator->errors()->toArray();

        // protocol_version is a unique 400 case per PROTOCOL.md § Versioning:
        // unknown versions → 400 unsupported_protocol_version, not 422.
        if (isset($errors['metadata.protocol_version']) || isset($errors['metadata'])) {
            $submitted = data_get($this->parsedMetadata, 'protocol_version');
            $expected = config('bugfab.protocol_version', '0.1');
            if ($submitted !== null && $submitted !== '' && $submitted !== $expected) {
                throw new HttpResponseException(Errors::json(
                    Errors::UNSUPPORTED_PROTOCOL_VERSION,
                    "Unknown protocol_version {$submitted}; expected {$expected}",
                    400
                ));
            }
        }

        // Missing required top-level parts (metadata / screenshot) → 400
        // validation_error per the standard error code table.
        if (isset($errors['metadata']) || isset($errors['screenshot'])) {
            throw new HttpResponseException(Errors::json(
                Errors::VALIDATION_ERROR,
                'metadata and screenshot are both required',
                400
            ));
        }

        // Everything else (bad severity, missing title, oversize reporter
        // field, ...) is a schema error.
        throw new HttpResponseException(Errors::json(
            Errors::SCHEMA_ERROR,
            $this->formatSchemaErrors($errors),
            422
        ));
    }

    /**
     * Reshape Laravel's flat-array validator errors into a list of
     * { field, message } entries — friendlier to consumers parsing the
     * response.
     */
    private function formatSchemaErrors(array $errors): array
    {
        $out = [];
        foreach ($errors as $field => $messages) {
            foreach ((array) $messages as $msg) {
                $out[] = ['field' => $field, 'message' => $msg];
            }
        }

        return $out;
    }

    public function metadata(): array
    {
        return $this->parsedMetadata ?? [];
    }

    public function expectedProtocolVersion(): string
    {
        return (string) config('bugfab.protocol_version', '0.1');
    }

    public function checkProtocolVersion(): void
    {
        $submitted = data_get($this->parsedMetadata, 'protocol_version');
        $expected = $this->expectedProtocolVersion();
        if ($submitted !== $expected) {
            throw new HttpResponseException(Errors::json(
                Errors::UNSUPPORTED_PROTOCOL_VERSION,
                "Unknown protocol_version " . var_export($submitted, true) . "; expected {$expected}",
                400
            ));
        }
    }
}
