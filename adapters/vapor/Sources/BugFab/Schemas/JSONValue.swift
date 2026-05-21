import Foundation

// A faithful JSON value type for the extra-allowed parts of the wire
// protocol (context.extras, source_mapping, console_errors entries).
// Round-trips opaque values verbatim.
public indirect enum BugFabJSONValue: Codable, Sendable, Equatable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([BugFabJSONValue])
    case object([String: BugFabJSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
            return
        }
        if let b = try? c.decode(Bool.self) {
            self = .bool(b); return
        }
        if let i = try? c.decode(Int.self) {
            self = .int(i); return
        }
        if let d = try? c.decode(Double.self) {
            self = .double(d); return
        }
        if let s = try? c.decode(String.self) {
            self = .string(s); return
        }
        if let a = try? c.decode([BugFabJSONValue].self) {
            self = .array(a); return
        }
        if let o = try? c.decode([String: BugFabJSONValue].self) {
            self = .object(o); return
        }
        throw DecodingError.dataCorruptedError(
            in: c, debugDescription: "Unrecognized JSON value")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .int(let i): try c.encode(i)
        case .double(let d): try c.encode(d)
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    // Helper container key for `extra=allow` style decoding.
    public struct DynamicKey: CodingKey, Hashable {
        public var stringValue: String
        public var intValue: Int? { nil }
        public init?(stringValue: String) {
            self.stringValue = stringValue
        }
        public init?(intValue: Int) { return nil }
    }
}
