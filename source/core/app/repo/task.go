package repo

import (
	"context"

	"github.com/1Panel-dev/1Panel/core/app/model"
	"github.com/1Panel-dev/1Panel/core/global"
	"gorm.io/gorm"
)

type TaskRepo struct {
}

type ITaskRepo interface {
	Save(ctx context.Context, task *model.Task) error
	GetFirst(opts ...global.DBOption) (model.Task, error)
	Page(page, size int, opts ...global.DBOption) (int64, []model.Task, error)
	Update(ctx context.Context, task *model.Task) error

	WithByID(id string) global.DBOption
	WithResourceID(id uint) global.DBOption
	WithOperate(taskOperate string) global.DBOption
}

func NewITaskRepo() ITaskRepo {
	return &TaskRepo{}
}

func getTaskDb(opts ...global.DBOption) *gorm.DB {
	db := global.TaskDB
	for _, opt := range opts {
		db = opt(db)
	}
	return db
}

func getTaskTx(ctx context.Context, opts ...global.DBOption) *gorm.DB {
	tx, ok := ctx.Value("db").(*gorm.DB)
	if ok {
		for _, opt := range opts {
			tx = opt(tx)
		}
		return tx
	}
	return getTaskDb(opts...)
}

func (t TaskRepo) WithByID(id string) global.DBOption {
	return func(g *gorm.DB) *gorm.DB {
		return g.Where("id = ?", id)
	}
}

func (t TaskRepo) WithOperate(taskOperate string) global.DBOption {
	return func(g *gorm.DB) *gorm.DB {
		return g.Where("operate = ?", taskOperate)
	}
}

func (t TaskRepo) WithResourceID(id uint) global.DBOption {
	return func(g *gorm.DB) *gorm.DB {
		return g.Where("resource_id = ?", id)
	}
}

func (t TaskRepo) Save(ctx context.Context, task *model.Task) error {
	return getTaskTx(ctx).Save(&task).Error
}

func (t TaskRepo) GetFirst(opts ...global.DBOption) (model.Task, error) {
	var task model.Task
	db := getTaskDb(opts...).Model(&model.Task{})
	if err := db.First(&task).Error; err != nil {
		return task, err
	}
	return task, nil
}

func (t TaskRepo) Page(page, size int, opts ...global.DBOption) (int64, []model.Task, error) {
	var tasks []model.Task
	db := getTaskDb(opts...).Model(&model.Task{})
	count := int64(0)
	db = db.Count(&count)
	err := db.Limit(size).Offset(size * (page - 1)).Find(&tasks).Error
	return count, tasks, err
}

func (t TaskRepo) Update(ctx context.Context, task *model.Task) error {
	return getTaskTx(ctx).Save(&task).Error
}
