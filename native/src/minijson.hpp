// NEXUS — minimal dependency-free JSON reader/writer (C++17).
// Written for the NEXUS graph analytics kernel so the native module has ZERO
// third-party dependencies (important for hackathon reproducibility: no network,
// no package manager, just `make`).
#pragma once

#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <utility>
#include <vector>

namespace nexus {

struct JValue {
  enum Type { NUL, BOOL, NUM, STR, ARR, OBJ } type = NUL;
  bool b = false;
  double num = 0.0;
  std::string str;
  std::vector<JValue> arr;
  std::vector<std::pair<std::string, JValue>> obj;

  bool is_null() const { return type == NUL; }
  bool is_num() const { return type == NUM; }
  bool is_str() const { return type == STR; }
  bool is_arr() const { return type == ARR; }
  bool is_obj() const { return type == OBJ; }

  const JValue* find(const std::string& key) const {
    if (type != OBJ) return nullptr;
    for (const auto& kv : obj)
      if (kv.first == key) return &kv.second;
    return nullptr;
  }

  double as_num(double dflt = 0.0) const { return type == NUM ? num : dflt; }
  std::string as_str(const std::string& dflt = "") const {
    return type == STR ? str : dflt;
  }
  bool as_bool(bool dflt = false) const {
    if (type == BOOL) return b;
    if (type == NUM) return num != 0.0;
    return dflt;
  }
  size_t size() const { return type == ARR ? arr.size() : 0; }
};

class JParser {
 public:
  explicit JParser(const std::string& s) : s_(s) {}

  bool parse(JValue& out) {
    skip_ws();
    if (!parse_value(out)) return false;
    skip_ws();
    return true;
  }

  const std::string& error() const { return err_; }

 private:
  const std::string& s_;
  size_t i_ = 0;
  std::string err_;

  void skip_ws() {
    while (i_ < s_.size() && (s_[i_] == ' ' || s_[i_] == '\t' || s_[i_] == '\n' || s_[i_] == '\r')) i_++;
  }

  bool fail(const std::string& m) {
    if (err_.empty()) err_ = m;
    return false;
  }

  bool parse_value(JValue& out) {
    skip_ws();
    if (i_ >= s_.size()) return fail("unexpected end of input");
    char c = s_[i_];
    switch (c) {
      case '{': return parse_obj(out);
      case '[': return parse_arr(out);
      case '"': {
        out.type = JValue::STR;
        return parse_string(out.str);
      }
      case 't':
        if (s_.compare(i_, 4, "true") == 0) {
          i_ += 4;
          out.type = JValue::BOOL;
          out.b = true;
          return true;
        }
        return fail("bad literal");
      case 'f':
        if (s_.compare(i_, 5, "false") == 0) {
          i_ += 5;
          out.type = JValue::BOOL;
          out.b = false;
          return true;
        }
        return fail("bad literal");
      case 'n':
        if (s_.compare(i_, 4, "null") == 0) {
          i_ += 4;
          out.type = JValue::NUL;
          return true;
        }
        return fail("bad literal");
      default: return parse_number(out);
    }
  }

  bool parse_number(JValue& out) {
    size_t start = i_;
    if (i_ < s_.size() && (s_[i_] == '-' || s_[i_] == '+')) i_++;
    bool any = false;
    while (i_ < s_.size() && (isdigit((unsigned char)s_[i_]) || s_[i_] == '.' || s_[i_] == 'e' || s_[i_] == 'E' ||
                             s_[i_] == '-' || s_[i_] == '+')) {
      if (isdigit((unsigned char)s_[i_])) any = true;
      i_++;
    }
    if (!any) return fail("invalid number");
    out.type = JValue::NUM;
    out.num = strtod(s_.substr(start, i_ - start).c_str(), nullptr);
    return true;
  }

  bool parse_string(std::string& out) {
    if (i_ >= s_.size() || s_[i_] != '"') return fail("expected string");
    i_++;
    out.clear();
    while (i_ < s_.size()) {
      char c = s_[i_++];
      if (c == '"') return true;
      if (c == '\\') {
        if (i_ >= s_.size()) return fail("bad escape");
        char e = s_[i_++];
        switch (e) {
          case '"': out.push_back('"'); break;
          case '\\': out.push_back('\\'); break;
          case '/': out.push_back('/'); break;
          case 'b': out.push_back('\b'); break;
          case 'f': out.push_back('\f'); break;
          case 'n': out.push_back('\n'); break;
          case 'r': out.push_back('\r'); break;
          case 't': out.push_back('\t'); break;
          case 'u': {
            if (i_ + 4 > s_.size()) return fail("bad unicode escape");
            unsigned code = (unsigned)strtoul(s_.substr(i_, 4).c_str(), nullptr, 16);
            i_ += 4;
            // NOTE: surrogate pairs are collapsed to '?' — the NEXUS corpus is
            // plain ASCII/UTF-8 passthrough, so this is only a safety net.
            if (code < 0x80) {
              out.push_back((char)code);
            } else if (code < 0x800) {
              out.push_back((char)(0xC0 | (code >> 6)));
              out.push_back((char)(0x80 | (code & 0x3F)));
            } else {
              out.push_back((char)(0xE0 | (code >> 12)));
              out.push_back((char)(0x80 | ((code >> 6) & 0x3F)));
              out.push_back((char)(0x80 | (code & 0x3F)));
            }
            break;
          }
          default: return fail("unknown escape");
        }
      } else {
        out.push_back(c);
      }
    }
    return fail("unterminated string");
  }

  bool parse_arr(JValue& out) {
    out.type = JValue::ARR;
    i_++;  // [
    skip_ws();
    if (i_ < s_.size() && s_[i_] == ']') {
      i_++;
      return true;
    }
    while (true) {
      JValue v;
      if (!parse_value(v)) return false;
      out.arr.push_back(std::move(v));
      skip_ws();
      if (i_ >= s_.size()) return fail("unterminated array");
      if (s_[i_] == ',') {
        i_++;
        continue;
      }
      if (s_[i_] == ']') {
        i_++;
        return true;
      }
      return fail("expected , or ]");
    }
  }

  bool parse_obj(JValue& out) {
    out.type = JValue::OBJ;
    i_++;  // {
    skip_ws();
    if (i_ < s_.size() && s_[i_] == '}') {
      i_++;
      return true;
    }
    while (true) {
      skip_ws();
      std::string key;
      if (!parse_string(key)) return false;
      skip_ws();
      if (i_ >= s_.size() || s_[i_] != ':') return fail("expected :");
      i_++;
      JValue v;
      if (!parse_value(v)) return false;
      out.obj.emplace_back(std::move(key), std::move(v));
      skip_ws();
      if (i_ >= s_.size()) return fail("unterminated object");
      if (s_[i_] == ',') {
        i_++;
        continue;
      }
      if (s_[i_] == '}') {
        i_++;
        return true;
      }
      return fail("expected , or }");
    }
  }
};

inline std::string jesc(const std::string& s) {
  std::string o;
  o.reserve(s.size() + 8);
  for (char c : s) {
    switch (c) {
      case '"': o += "\\\""; break;
      case '\\': o += "\\\\"; break;
      case '\n': o += "\\n"; break;
      case '\r': o += "\\r"; break;
      case '\t': o += "\\t"; break;
      default:
        if ((unsigned char)c < 0x20) {
          char buf[8];
          snprintf(buf, sizeof(buf), "\\u%04x", (unsigned char)c);
          o += buf;
        } else {
          o.push_back(c);
        }
    }
  }
  return o;
}

inline std::string jnum(double v) {
  if (!std::isfinite(v)) return "null";
  char buf[40];
  double r = std::round(v);
  if (std::fabs(v - r) < 1e-12 && std::fabs(v) < 1e15) {
    snprintf(buf, sizeof(buf), "%.0f", r);
  } else {
    snprintf(buf, sizeof(buf), "%.10g", v);
  }
  return std::string(buf);
}

class JWriter {
 public:
  void raw(const std::string& s) { out_ += s; }
  void key(const std::string& k, bool first) {
    if (!first) out_ += ",";
    out_ += "\"" + jesc(k) + "\":";
  }
  void str(const std::string& v) { out_ += "\"" + jesc(v) + "\""; }
  void num(double v) { out_ += jnum(v); }
  void boolean(bool v) { out_ += v ? "true" : "false"; }
  const std::string& str() const { return out_; }

 private:
  std::string out_;
};

}  // namespace nexus
