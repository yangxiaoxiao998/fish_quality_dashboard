//我要用哪些规则包来检查代码
module.exports = {
  root: true,
  //外部规则包
  extends: [
    //标准 CSS 规范
    'stylelint-config-standard',
    //SCSS 支持
    'stylelint-config-standard-scss',
    //支持 .vue 文件
    'stylelint-config-recommended-vue/scss',
    //CSS 属性排序规则
    //'stylelint-config-recess-order',
    //避免和 prettier 冲突
    //'stylelint-config-prettier',
  ],
  rules: {
    'value-keyword-case': null,
    'block-no-empty': null,
    'color-function-notation': null,
    'function-url-quotes': null,
    'property-no-vendor-prefix': null,
    'rule-empty-line-before': 'never',
    'font-family-no-missing-generic-family-keyword': null,
    'no-descending-specificity': null,
    'declaration-block-single-line-max-declarations': null,
  },
  overrides: [
    {
      files: ['**/*.{vue,html}'],
      customSyntax: 'postcss-html',
    },
        // ✅ 新增：对字体/生成 CSS 放宽格式规则
    {
      files: ['src/assets/fonts/**/*.css'],
      rules: {
        'at-rule-empty-line-before': null,
      },
    },
  ],
  "plugins": ["stylelint-order"],
}
