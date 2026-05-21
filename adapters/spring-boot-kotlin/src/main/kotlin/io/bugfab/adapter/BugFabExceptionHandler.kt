package io.bugfab.adapter

import com.fasterxml.jackson.core.JsonProcessingException
import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.http.converter.HttpMessageNotReadableException
import org.springframework.web.bind.MethodArgumentNotValidException
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import org.springframework.web.multipart.MaxUploadSizeExceededException

/**
 * Maps Spring MVC's stock exceptions onto the Bug-Fab wire-protocol
 * error envelope.
 *
 * Two surprises here for adapter authors coming from FastAPI:
 *
 *  1. `@Valid` failures throw [MethodArgumentNotValidException] — NOT
 *     `ConstraintViolationException`. The latter fires for non-body
 *     `@Validated` params (path / query). The intake endpoint never
 *     hits that path, but the status-update body does, so we map both.
 *
 *  2. Spring will translate an oversize multipart into
 *     [MaxUploadSizeExceededException] BEFORE the controller body
 *     runs, if the part exceeds `spring.servlet.multipart.max-file-size`.
 *     That happens when the request's Content-Length exceeds the cap.
 *     Our controller-level size check inside `submit()` still fires
 *     for the case where the part is at the limit and we want the
 *     spec's documented `limit_bytes` field on the envelope, so both
 *     paths agree on the `payload_too_large` error code.
 */
@RestControllerAdvice
class BugFabExceptionHandler(
    private val properties: BugFabProperties,
) {

    @ExceptionHandler(MethodArgumentNotValidException::class)
    fun handleValidation(ex: MethodArgumentNotValidException): ResponseEntity<ErrorEnvelope> {
        val detail = ex.bindingResult.fieldErrors
            .joinToString("; ") { "${it.field}: ${it.defaultMessage ?: "invalid"}" }
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY)
            .body(ErrorEnvelope("schema_error", detail.ifEmpty { "validation failed" }))
    }

    @ExceptionHandler(IllegalArgumentException::class)
    fun handleIllegalArgument(ex: IllegalArgumentException): ResponseEntity<ErrorEnvelope> {
        // Severity / Status enum coercion failures land here when Jackson
        // throws inside `@JsonCreator`. The Python adapter returns 422 for
        // the same shape, so we mirror that.
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY)
            .body(ErrorEnvelope("schema_error", ex.message ?: "invalid value"))
    }

    @ExceptionHandler(JsonProcessingException::class)
    fun handleJsonError(ex: JsonProcessingException): ResponseEntity<ErrorEnvelope> {
        // Malformed JSON in the request body for `PUT /reports/{id}/status`.
        // (Malformed metadata-JSON in intake is caught inside the controller
        // because we explicitly distinguish "metadata part missing" from
        // "metadata part unparseable".)
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
            .body(ErrorEnvelope("validation_error", ex.originalMessage ?: "malformed JSON"))
    }

    @ExceptionHandler(HttpMessageNotReadableException::class)
    fun handleNotReadable(ex: HttpMessageNotReadableException): ResponseEntity<ErrorEnvelope> {
        // Spring wraps Jackson deserialization failures (including the
        // `IllegalArgumentException` our enum `@JsonCreator` methods throw
        // for unknown wire values) in this exception during `@RequestBody`
        // binding. Differentiate the two cases by inspecting the root
        // cause: enum-coercion failures are schema errors (422), while a
        // malformed JSON token is a validation error (400).
        val root = generateSequence<Throwable>(ex) { it.cause }.last()
        return if (root is IllegalArgumentException) {
            ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY)
                .body(ErrorEnvelope("schema_error", root.message ?: "invalid value"))
        } else {
            ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(ErrorEnvelope("validation_error", root.message ?: "malformed request body"))
        }
    }

    @ExceptionHandler(MaxUploadSizeExceededException::class)
    fun handleOversizeUpload(ex: MaxUploadSizeExceededException): ResponseEntity<ErrorEnvelope> {
        val maxBytes = properties.maxScreenshotMb.toLong() * 1024 * 1024
        return ResponseEntity.status(HttpStatus.PAYLOAD_TOO_LARGE)
            .body(
                ErrorEnvelope(
                    error = "payload_too_large",
                    detail = "Upload exceeds maximum size of ${properties.maxScreenshotMb} MiB",
                    limitBytes = maxBytes,
                )
            )
    }
}
