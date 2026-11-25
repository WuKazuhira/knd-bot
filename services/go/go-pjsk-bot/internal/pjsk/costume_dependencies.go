package pjsk

import (
	"fmt"
	"os"
	"path"
	"strings"
)

// 只检查 3D 引擎实际加载的依赖路径，跳过 Unity 原始诊断字段
func (r *CostumeRenderer) validateRuntimeDependencies(file, base string, value any, visited map[string]bool, depth int) error {
	if depth > 24 {
		return fmt.Errorf("runtime dependency depth exceeded")
	}
	var walk func(any, string) error
	walk = func(v any, key string) error {
		switch x := v.(type) {
		case map[string]any:
			for k, child := range x {
				if err := walk(child, k); err != nil {
					return err
				}
			}
		case []any:
			for _, child := range x {
				if err := walk(child, key); err != nil {
					return err
				}
			}
		case string:
			transport := key == "corePath" || key == "unityMotionJson" || key == "meshUrl" || key == "skeletonUrl" || key == "animationUrls" || key == "mainTex" || key == "shadowTex" || key == "valueTex" || key == "faceShadowTex" || key == "uri"
			if !transport || x == "" {
				return nil
			}
			if strings.Contains(x, ":") {
				return fmt.Errorf("unsupported external runtime dependency %s", x)
			}
			resourcePath := strings.TrimPrefix(x, "/")
			if key == "corePath" || key == "unityMotionJson" {
				resourcePath = x
			}
			target := resourcePath
			if key == "unityMotionJson" {
				target = path.Join(path.Dir(file), x)
			} else if key != "corePath" && base != "" && !strings.HasPrefix(x, "/") && !strings.HasPrefix(x, base+"/") {
				target = base + "/" + resourcePath
			}
			// 做根目录边界检查前不能先 Clean 掉路径穿越
			if strings.Contains(x, "\\") {
				return fmt.Errorf("unsafe dependency %s", x)
			}
			for _, segment := range strings.Split(x, "/") {
				if segment == ".." || segment == "." {
					return fmt.Errorf("unsafe dependency %s", x)
				}
			}
			if visited[target] {
				return nil
			}
			visited[target] = true
			if strings.HasSuffix(target, ".msgpack.br") {
				metadata, err := r.metadata(target)
				if err != nil {
					return fmt.Errorf("dependency %s: %w", target, err)
				}
				if err = r.validateRuntimeDependencies(target, base, metadata, visited, depth+1); err != nil {
					return err
				}
			} else {
				full, err := r.runtimeFile(target)
				if err != nil {
					return fmt.Errorf("dependency %s: %w", target, err)
				}
				info, err := os.Stat(full)
				if err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
					return fmt.Errorf("runtime dependency %s unavailable/empty", target)
				}
			}
		}
		return nil
	}
	return walk(value, "")
}
