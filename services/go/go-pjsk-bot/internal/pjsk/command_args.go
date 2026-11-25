package pjsk

import (
	"fmt"
	"strings"
	"unicode"
)

func splitWorldBloomArgs(raw string) ([]string, error) {
	var tokens []string
	var token strings.Builder
	var quote rune
	escaped, started := false, false
	for _, r := range raw {
		if escaped {
			// 双引号里只转义引号和反斜线，其余保留
			if quote == '"' && r != '"' && r != '\\' {
				token.WriteRune('\\')
			}
			token.WriteRune(r)
			escaped = false
			started = true
			continue
		}
		if r == '\\' && quote != '\'' {
			escaped = true
			started = true
			continue
		}
		if quote != 0 {
			if r == quote {
				quote = 0
			} else {
				token.WriteRune(r)
			}
			continue
		}
		switch {
		case r == '\'' || r == '"':
			quote = r
			started = true
		case unicode.IsSpace(r):
			if started {
				tokens = append(tokens, token.String())
				token.Reset()
				started = false
			}
		default:
			token.WriteRune(r)
			started = true
		}
	}
	if quote != 0 || escaped {
		return nil, fmt.Errorf("参数引号或转义不匹配，请检查命令后重试")
	}
	if started {
		tokens = append(tokens, token.String())
	}
	return tokens, nil
}
