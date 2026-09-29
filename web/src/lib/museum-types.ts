export type MuseumObject = {id: string; title: string; source_url: string; image_url?: string; display_title?: string; collection?: string; has_narration?: boolean; source_kind?: string};

const names: Record<string, string> = {"artic-28560": "卧室", "artic-16568": "睡莲", "artic-27992": "大碗岛的星期天下午"};
export function workName(work: {id: string; title: string; display_title?: string}) {
  return work.display_title || names[work.id] || work.title;
}
