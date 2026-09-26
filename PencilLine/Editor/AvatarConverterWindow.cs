using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace PencilLine.EditorTools
{
    /// <summary>
    /// アバター (シーン上 / プリファブ) をドラッグ&ドロップして、全マテリアルを一括変換する。
    /// 元のアバターとマテリアルは変更せず、変換後の複製 (プリファブならバリアント) を作ります。
    /// </summary>
    public class AvatarConverterWindow : EditorWindow
    {
        enum Action
        {
            Flat,
            NoOutline,
            Keep,
        }

        static readonly string[] ActionLabels = { "フラット化", "元のまま・アウトラインだけオフ", "変更しない" };

        class Row
        {
            public Material mat;
            public string kind;
            public int users;
            public bool transparent;
            public Action action;
            public Material result;
            public string note;
        }

        [MenuItem("Tools/PencilLine/Avatar Converter")]
        static void Open()
        {
            GetWindow<AvatarConverterWindow>("PencilLine 一括変換");
        }

        GameObject _target;
        Action _defaultAction = Action.Flat;
        bool _transparentKeep = true;
        bool _hideOriginal = true;
        bool _placeInScene = true;
        string _outputRoot = "Assets/PencilLine_Converted";
        bool _showFlatSettings;
        // 一括変換の既定値 (色を多めに取り、書き影の自動統合はほぼ切る)
        readonly PaletteSettings _settings = new PaletteSettings
        {
            clusterCount = 32,
            hueTolerance = 1f,
            maxLDiff = 0.5f,
            maxLDiffAchromatic = 0.5f,
        };
        readonly List<Row> _rows = new List<Row>();
        Vector2 _scroll;
        GameObject _lastResult;

        void OnGUI()
        {
            DrawDropArea();

            EditorGUI.BeginChangeCheck();
            _target = (GameObject)EditorGUILayout.ObjectField("アバター", _target, typeof(GameObject), true);
            if (EditorGUI.EndChangeCheck()) Scan();
            if (_target == null) return;

            bool isAsset = EditorUtility.IsPersistent(_target);
            EditorGUILayout.LabelField(isAsset
                ? "プリファブ → 変換済みの「プリファブバリアント」を作ります"
                : "シーン上のオブジェクト → 変換済みの「複製」を隣に作ります", EditorStyles.miniLabel);

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("変換方法", EditorStyles.boldLabel);
            EditorGUI.BeginChangeCheck();
            _defaultAction = (Action)EditorGUILayout.Popup("基本の変換", (int)_defaultAction, ActionLabels);
            _transparentKeep = EditorGUILayout.ToggleLeft(
                new GUIContent("半透明マテリアル (目のハイライト・頬染めなど) は元のまま", "フラット化は切り抜きしかできないため"),
                _transparentKeep);
            if (EditorGUI.EndChangeCheck()) ApplyDefaults();

            _showFlatSettings = EditorGUILayout.Foldout(_showFlatSettings, "フラット化の設定", true);
            if (_showFlatSettings)
            {
                using (new EditorGUI.IndentLevelScope())
                {
                    _settings.clusterCount = EditorGUILayout.IntSlider("色の数", _settings.clusterCount, 2, PaletteBuilder.MaxPalette);
                    _settings.hueTolerance = EditorGUILayout.Slider("色相の許容差 (度)", _settings.hueTolerance, 0f, 90f);
                    _settings.maxLDiff = EditorGUILayout.Slider("明度差の上限 (有彩色)", _settings.maxLDiff, 0f, 1f);
                    _settings.maxLDiffAchromatic = EditorGUILayout.Slider("明度差の上限 (白黒灰)", _settings.maxLDiffAchromatic, 0f, 1f);
                    _settings.bakeLilToonLayers = EditorGUILayout.Toggle("lilToon のレイヤーを焼き込む", _settings.bakeLilToonLayers);
                    _settings.bakeScale = EditorGUILayout.IntPopup("焼き込み解像度", _settings.bakeScale,
                        new[] { "メインと同じ", "2倍" }, new[] { 1, 2 });
                    _settings.useMaterialShade = EditorGUILayout.Toggle("MToon の影色を使う", _settings.useMaterialShade);
                    FlatPaletteWindow.DrawCleanEdgeSettings(_settings);
                    _settings.flatness = EditorGUILayout.Slider(new GUIContent("フラット度",
                        "1 = 完全にフラット。下げると元の塗りの濃淡が戻ります (変換後もマテリアルで変えられます)"), _settings.flatness, 0f, 1f);
                }
            }

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("出力", EditorStyles.boldLabel);
            _outputRoot = EditorGUILayout.TextField("出力フォルダ", _outputRoot);
            if (isAsset) _placeInScene = EditorGUILayout.ToggleLeft("作ったバリアントをシーンにも置く", _placeInScene);
            else _hideOriginal = EditorGUILayout.ToggleLeft("元のオブジェクトを非表示にする", _hideOriginal);

            DrawRows();

            EditorGUILayout.Space();
            using (new EditorGUI.DisabledScope(_rows.Count == 0))
            {
                if (GUILayout.Button($"変換する ({_rows.Count} マテリアル)", GUILayout.Height(32))) Convert();
            }

            if (_lastResult != null)
            {
                EditorGUILayout.Space();
                using (new EditorGUI.DisabledScope(true))
                {
                    EditorGUILayout.ObjectField("変換結果", _lastResult, typeof(GameObject), true);
                }
            }
        }

        void DrawDropArea()
        {
            Rect r = GUILayoutUtility.GetRect(0, 56, GUILayout.ExpandWidth(true));
            var style = new GUIStyle(EditorStyles.helpBox)
            {
                alignment = TextAnchor.MiddleCenter,
                fontSize = 13,
            };
            GUI.Box(r, "ここにアバターをドラッグ&ドロップ\n(シーン上のオブジェクト / プリファブ / VRM・FBX)", style);

            Event e = Event.current;
            if (!r.Contains(e.mousePosition)) return;
            if (e.type != EventType.DragUpdated && e.type != EventType.DragPerform) return;

            GameObject go = null;
            foreach (var o in DragAndDrop.objectReferences)
            {
                if (o is GameObject g)
                {
                    go = g;
                    break;
                }
            }
            if (go == null) return;

            DragAndDrop.visualMode = DragAndDropVisualMode.Copy;
            if (e.type == EventType.DragPerform)
            {
                DragAndDrop.AcceptDrag();
                _target = go;
                Scan();
            }
            e.Use();
        }

        void DrawRows()
        {
            if (_rows.Count == 0) return;

            EditorGUILayout.Space();
            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUILayout.LabelField($"マテリアル ({_rows.Count})", EditorStyles.boldLabel);
                if (GUILayout.Button("全部フラット化", EditorStyles.miniButton)) SetAll(Action.Flat);
                if (GUILayout.Button("全部アウトラインだけオフ", EditorStyles.miniButton)) SetAll(Action.NoOutline);
            }

            _scroll = EditorGUILayout.BeginScrollView(_scroll, GUILayout.MinHeight(120), GUILayout.MaxHeight(360));
            foreach (var row in _rows)
            {
                using (new EditorGUILayout.HorizontalScope())
                {
                    using (new EditorGUI.DisabledScope(true))
                    {
                        EditorGUILayout.ObjectField(row.mat, typeof(Material), false, GUILayout.MinWidth(120));
                    }
                    GUILayout.Label(row.kind, EditorStyles.miniLabel, GUILayout.Width(110));
                    row.action = (Action)EditorGUILayout.Popup((int)row.action, ActionLabels, GUILayout.Width(170));

                    if (row.result != null)
                    {
                        if (row.action == Action.Flat && PaletteBuilder.IsFlat(row.result))
                        {
                            if (GUILayout.Button("調整", EditorStyles.miniButton, GUILayout.Width(40)))
                            {
                                FlatPaletteWindow.OpenWith(row.mat, row.result, _settings);
                            }
                        }
                        else
                        {
                            GUILayout.Space(44);
                        }
                    }
                }
                if (!string.IsNullOrEmpty(row.note))
                {
                    EditorGUILayout.LabelField("   " + row.note, EditorStyles.wordWrappedMiniLabel);
                }
            }
            EditorGUILayout.EndScrollView();
        }

        // ------------------------------------------------------------
        // スキャン
        // ------------------------------------------------------------
        void Scan()
        {
            _rows.Clear();
            _lastResult = null;
            if (_target == null) return;

            var map = new Dictionary<Material, Row>();
            foreach (var r in _target.GetComponentsInChildren<Renderer>(true))
            {
                if (!(r is SkinnedMeshRenderer) && !(r is MeshRenderer)) continue;
                foreach (var m in r.sharedMaterials)
                {
                    if (m == null) continue;
                    if (!map.TryGetValue(m, out var row))
                    {
                        row = new Row { mat = m };
                        map.Add(m, row);
                        _rows.Add(row);
                    }
                    row.users++;
                }
            }

            foreach (var row in _rows)
            {
                var info = MaterialAdapter.Read(row.mat);
                row.transparent = info.alpha == AlphaKind.Transparent && !info.transparentWithZWrite;
                string kind = info.family == ShaderFamily.MToon0 ? "MToon"
                            : info.family == ShaderFamily.MToon10 ? "MToon 1.0"
                            : PaletteBuilder.IsFlat(row.mat) ? "Flat (変換済み)"
                            : LilToonBaker.IsLilToon(row.mat) ? "lilToon"
                            : (row.mat.shader != null ? ShortName(row.mat.shader.name) : "?");
                if (row.transparent) kind += " / 半透明";
                row.kind = kind;
            }
            ApplyDefaults();
        }

        static string ShortName(string shaderName)
        {
            int i = shaderName.LastIndexOf('/');
            return i >= 0 ? shaderName.Substring(i + 1) : shaderName;
        }

        void ApplyDefaults()
        {
            foreach (var row in _rows)
            {
                if (PaletteBuilder.IsFlat(row.mat)) row.action = Action.Keep;
                else if (row.transparent && _transparentKeep && _defaultAction == Action.Flat) row.action = Action.NoOutline;
                else row.action = _defaultAction;
                row.result = null;
                row.note = null;
            }
        }

        void SetAll(Action a)
        {
            foreach (var row in _rows)
            {
                if (PaletteBuilder.IsFlat(row.mat)) continue;
                row.action = a;
            }
        }

        // ------------------------------------------------------------
        // 変換
        // ------------------------------------------------------------
        void Convert()
        {
            string outDir = $"{_outputRoot.TrimEnd('/')}/{Sanitize(_target.name)}";
            PaletteBuilder.EnsureFolder(outDir);

            var map = new Dictionary<Material, Material>();
            bool cancelled = false;
            try
            {
                for (int i = 0; i < _rows.Count; i++)
                {
                    var row = _rows[i];
                    if (EditorUtility.DisplayCancelableProgressBar("PencilLine 一括変換",
                            $"{row.mat.name}  ({i + 1}/{_rows.Count})", (float)i / _rows.Count))
                    {
                        cancelled = true;
                        break;
                    }

                    row.result = null;
                    row.note = null;
                    switch (row.action)
                    {
                        case Action.Flat:
                        {
                            var b = new PaletteBuilder(_settings.Clone()) { outputDir = outDir, uniqueFileNames = true };
                            if (b.Extract(row.mat, false, out string err))
                            {
                                row.result = b.CreateOrUpdateFlat(null, false);
                                if (b.warnings.Count > 0) row.note = string.Join(" / ", b.warnings);
                            }
                            else
                            {
                                row.note = "スキップ: " + err;
                            }
                            break;
                        }
                        case Action.NoOutline:
                        {
                            row.result = OutlineUtil.CreateNoOutlineCopy(row.mat, outDir, out string how);
                            row.note = how;
                            break;
                        }
                    }
                    if (row.result != null) map[row.mat] = row.result;
                }
            }
            finally
            {
                EditorUtility.ClearProgressBar();
                AssetDatabase.SaveAssets();
            }

            if (cancelled)
            {
                ShowNotification(new GUIContent("中断しました (作成済みのマテリアルは出力フォルダに残っています)"));
                return;
            }
            _lastResult = CreateOutput(map, outDir);
            if (_lastResult != null) EditorGUIUtility.PingObject(_lastResult);
        }

        GameObject CreateOutput(Dictionary<Material, Material> map, string outDir)
        {
            if (EditorUtility.IsPersistent(_target))
            {
                // プリファブ → バリアントを作る (元のプリファブは変更しない)
                var inst = (GameObject)PrefabUtility.InstantiatePrefab(_target);
                if (inst == null)
                {
                    ShowNotification(new GUIContent("プリファブを開けませんでした"));
                    return null;
                }
                Remap(inst, map);
                string path = AssetDatabase.GenerateUniqueAssetPath($"{outDir}/{Sanitize(_target.name)}_PencilLine.prefab");
                var variant = PrefabUtility.SaveAsPrefabAsset(inst, path);
                Object.DestroyImmediate(inst);
                if (variant == null) return null;

                if (_placeInScene)
                {
                    var placed = (GameObject)PrefabUtility.InstantiatePrefab(variant);
                    Undo.RegisterCreatedObjectUndo(placed, "Place Converted Avatar");
                    Selection.activeGameObject = placed;
                    return placed;
                }
                return variant;
            }
            else
            {
                // シーン上 → 複製を隣に作る
                var copy = Object.Instantiate(_target, _target.transform.parent);
                copy.name = _target.name + "_PencilLine";
                copy.transform.SetSiblingIndex(_target.transform.GetSiblingIndex() + 1);
                Undo.RegisterCreatedObjectUndo(copy, "Convert Avatar");
                Remap(copy, map);
                copy.SetActive(true);
                if (_hideOriginal)
                {
                    Undo.RecordObject(_target, "Hide Original Avatar");
                    _target.SetActive(false);
                }
                Selection.activeGameObject = copy;
                return copy;
            }
        }

        static void Remap(GameObject root, Dictionary<Material, Material> map)
        {
            foreach (var r in root.GetComponentsInChildren<Renderer>(true))
            {
                var mats = r.sharedMaterials;
                bool hit = false;
                for (int i = 0; i < mats.Length; i++)
                {
                    if (mats[i] != null && map.TryGetValue(mats[i], out var to))
                    {
                        mats[i] = to;
                        hit = true;
                    }
                }
                if (hit) r.sharedMaterials = mats;
            }
        }

        static string Sanitize(string name)
        {
            foreach (char c in Path.GetInvalidFileNameChars()) name = name.Replace(c, '_');
            return name;
        }
    }
}
