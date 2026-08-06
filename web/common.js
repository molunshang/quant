const $ = id => document.getElementById(id);

// 顶部导航：按 body[data-page] 高亮当前页
document.querySelectorAll('.nav-link').forEach(a => {
  if (a.dataset.page === document.body.dataset.page) a.classList.add('active');
});
