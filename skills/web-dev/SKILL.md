---
name: web-dev
description: 网页开发技能，包含 HTML/CSS/JS 最佳实践和常用模板
---

# Web Development Skill

## 编码规范

### HTML
- 使用语义化 HTML5 标签（`<header>`, `<nav>`, `<main>`, `<section>`, `<footer>`）
- 始终设置 `lang` 属性
- 图片必须包含 `alt` 属性
- 表单元素必须关联 `<label>`

### CSS
- 优先使用 Flexbox/Grid 布局
- 使用 CSS 变量管理主题色
- 移动端优先（Mobile First）响应式设计
- 避免使用 `!important`

### JavaScript
- 使用 ES6+ 语法（箭头函数、解构、模板字符串）
- 使用 `const`/`let`，避免 `var`
- 异步操作使用 `async/await`
- 错误处理使用 `try/catch`

## 常用模板

### HTML 基础模板
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>页面标题</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <header>
        <nav><!-- 导航 --></nav>
    </header>
    <main>
        <!-- 主要内容 -->
    </main>
    <footer>
        <!-- 页脚 -->
    </footer>
    <script src="script.js"></script>
</body>
</html>
```

### CSS Reset
```css
*, *::before, *::after {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.6;
    color: #333;
}

img { max-width: 100%; height: auto; }
```

## 调试技巧
- 使用浏览器 DevTools 的 Elements 面板检查 DOM
- 使用 Console 面板查看 JavaScript 错误
- 使用 Network 面板检查资源加载
- 使用 Lighthouse 进行性能审计
