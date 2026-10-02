package pjsk

import (
	"fmt"
	"strings"
	"unicode"

	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// splitWorldBloomArgs 对齐 Python shlex 的引号、转义及拼接规则，不影响全局路由拆词。
func splitWorldBloomArgs(raw string) ([]string, error) {
	var tokens []string
	var token strings.Builder
	var quote rune
	escaped, started := false, false
	for _, r := range raw {
		if escaped {
			// 双引号内只有引号和反斜线被转义，其余保留反斜线。
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

// explicitRemoteRegion 只有 cn/tw 命令前缀是显式区服；无前缀仍使用配置。
func explicitRemoteRegion(req router.Request) string {
	raw := strings.ToLower(strings.TrimSpace(req.RawCmd))
	if strings.HasPrefix(raw, "cn") && req.Server == router.ServerCN {
		return "cn"
	}
	if strings.HasPrefix(raw, "tw") && req.Server == router.ServerTW {
		return "tw"
	}
	return ""
}

func (m *RemoteModule) validateCommandTarget(req router.Request) error {
	if err := validateRegion(m.region); err != nil {
		return err
	}
	if region := explicitRemoteRegion(req); region != "" && region != m.region {
		return fmt.Errorf("当前 remote/live 固定使用 %s 配置目标，不支持 %s 前缀；未切换账号，请使用无前缀命令或由管理员调整配置", strings.ToUpper(m.region), strings.ToUpper(region))
	}
	return nil
}

func tokenCommandRegion(req router.Request, defaultRegion string) (string, error) {
	prefix := explicitRemoteRegion(req)
	if prefix != "" {
		if arg := strings.ToLower(strings.TrimSpace(req.Arg)); arg != "" && arg != prefix {
			return "", fmt.Errorf("区服前缀 %s 与参数 %s 不一致，请只指定一个目标区服", strings.ToUpper(prefix), arg)
		}
		return commandRegion(req.Arg, prefix)
	}
	return commandRegion(req.Arg, defaultRegion)
}
