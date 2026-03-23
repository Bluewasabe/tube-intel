/* === TubeIntel — Shared JS === */

// ── Category color mapping ──────────────────────────────────────────────────

const CATEGORY_COLORS = {
  homelab: 'var(--cat-homelab)',
  new_project: 'var(--cat-new-project)',
  apply_to_existing: 'var(--cat-apply)',
  learning: 'var(--cat-learning)',
  velvet_verve: 'var(--cat-velvet)',
  low_value: 'var(--cat-low)'
};

function categoryColor(cat) {
  return CATEGORY_COLORS[cat] || 'var(--cat-low)';
}

function formatCategory(cat) {
  if (!cat) return 'uncategorized';
  return cat.replace(/_/g, ' ');
}

// ── Date formatting ─────────────────────────────────────────────────────────

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function formatDate(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    return MONTHS[d.getMonth()] + ' ' + d.getDate();
  } catch {
    return isoStr;
  }
}

// ── HTML escaping ───────────────────────────────────────────────────────────

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── Card rendering ──────────────────────────────────────────────────────────

function renderCard(video, index) {
  const card = document.createElement('a');
  card.href = '/video/' + video.id;
  card.className = 'video-card';
  if (video.status === 'pending' || video.status === 'processing') {
    card.className += ' card-status-' + video.status;
  }

  // Category border color
  const catColor = categoryColor(video.category);
  card.style.borderLeftColor = catColor.startsWith('var(') ?
    getComputedStyle(document.documentElement).getPropertyValue(catColor.slice(4, -1)) || '#4b5563' :
    catColor;

  // Stagger animation
  card.style.animationDelay = (Math.min(index, 10) * 0.05) + 's';

  // Thumbnail
  let thumbHtml = '';
  if (video.thumbnail_url) {
    thumbHtml = '<div class="card-thumb"><img src="' + escapeHtml(video.thumbnail_url) +
      '" alt="" loading="lazy" /></div>';
  } else {
    thumbHtml = '<div class="card-thumb"></div>';
  }

  // Body
  const title = escapeHtml(video.title || 'Untitled');
  const channel = video.channel_name ? '<div class="card-channel">' + escapeHtml(video.channel_name) + '</div>' : '';
  const summary = video.summary ? '<div class="card-summary">' + escapeHtml(video.summary) + '</div>' : '';

  // Footer badges
  let badges = '';
  if (video.category) {
    badges += '<span class="badge badge-category" style="--cat-color:' + categoryColor(video.category) + '">' +
      formatCategory(video.category) + '</span>';
  }
  if (video.status && video.status !== 'done') {
    badges += '<span class="badge detail-status status-' + video.status + '">' + video.status + '</span>';
  }
  badges += '<span class="badge badge-source badge-' + (video.source || 'manual') + '">' +
    escapeHtml(video.source || 'manual') + '</span>';

  const date = video.created_at ? '<span class="card-date">' + formatDate(video.created_at) + '</span>' : '';

  card.innerHTML =
    thumbHtml +
    '<div class="card-body">' +
      '<div class="card-title">' + title + '</div>' +
      channel +
      summary +
      '<div class="card-footer">' + badges + date + '</div>' +
    '</div>';

  return card;
}
